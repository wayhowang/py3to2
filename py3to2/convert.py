from tokenize import Name
from typing import *
import libcst as cst
import libcst.metadata as cstmeta
import lib2to3.refactor as refactor # yes, lib2to3. It calls fixes defined in lib3to2
import sys
import argparse
import os
import base64

import pytype
from . import expression_type
import pytype.pytd.pytd as pytd


def get_latest_comment(node: cst.SimpleStatementLine):
    for line in reversed(node.leading_lines):
        if not isinstance(line, cst.EmptyLine):
            continue
        if line.comment is None:
            continue
        comment = line.comment.value
        if comment is not None:
            return comment
    return None

def get_comment_value(s: str):
    return s[1:].strip()

class AddHeader(cst.CSTTransformer):
    def leave_Module(
        self, original_node: cst.Module, updated_node: cst.Module
    ):
        stmt = cst.EmptyLine(indent=False, comment=cst.Comment('# coding: utf8'))
        return updated_node.with_changes(header = (stmt, ) + tuple(updated_node.header))

class AddImports(cst.CSTTransformer):
    def leave_Module(
        self, original_node: cst.Module, updated_node: cst.Module
    ):
        stmt1 = cst.parse_statement('from __future__ import absolute_import, division, print_function, unicode_literals')
        return updated_node.with_changes(body = (stmt1, ) + tuple(updated_node.body))

class Annotate(cst.CSTTransformer):
    def leave_SimpleStatementLine(
        self, original_node: cst.SimpleStatementLine, updated_node: cst.SimpleStatementLine
    ):
        comment = get_latest_comment(updated_node)
        if comment is None:
            return updated_node
        comment = get_comment_value(comment)
        prefix = 'pyc:'
        if not comment.startswith(prefix):
            return updated_node
        comment = comment[len(prefix):].strip()
        if comment == 'skip':
            return cst.RemovalSentinel.REMOVE
        return updated_node

class RemoveTypehint(cst.CSTTransformer):
    METADATA_DEPENDENCIES = (cstmeta.PositionProvider, )

    REPLACE_MAPPING = {
        'typing': '_py3to2_typing',
        'typing_extensions': '_py3to2_typing_extensions'
    }

    def __init__(self, relative_dots: int, type_info: Dict[expression_type.CodePosition, pytd.Type]):
        super().__init__()
        self._relative_dots = relative_dots
        self._type_info = type_info

    # ==========================================
    # 删除变量、参数、函数的类型提示

    def leave_AnnAssign(
        self, original_node: cst.AnnAssign, updated_node: cst.AnnAssign
    ):
        if updated_node.value is None:
            return cst.RemovalSentinel.REMOVE
        else:
            return cst.Assign([cst.AssignTarget(updated_node.target)], updated_node.value, updated_node.semicolon)
       
    def leave_Param(self, original_node, updated_node: cst.Param):
        return updated_node.with_changes(annotation=None)

    def leave_FunctionDef(self, original_node, updated_node: cst.FunctionDef):
        return updated_node.with_changes(returns=None)

    # =========================================
    # 删除和 typing 相关的 import

    def leave_Import(self, original_node: cst.Import, updated_node: cst.Import) -> Union[cst.BaseSmallStatement, cst.FlattenSentinel[cst.BaseSmallStatement], cst.RemovalSentinel]:
        imports = list(updated_node.names)
        new_imports = []
        replaced_imports: List[cst.BaseSmallStatement] = []
        for import_alias in imports:    
            if import_alias.name is None:
                new_imports.append(import_alias)
                continue

            new_import_name = RemoveTypehint.REPLACE_MAPPING.get(str(import_alias.name.value))
            if new_import_name is None:
                new_imports.append(import_alias.with_changes(comma=cst.MaybeSentinel.DEFAULT))
                continue
                
            replaced_imports.append(cst.ImportFrom(
                module=None,
                relative=[cst.Dot() for _ in range(self._relative_dots)],
                names=[import_alias.with_changes(name=cst.Name(value=new_import_name), comma=cst.MaybeSentinel.DEFAULT)],
                semicolon=cst.MaybeSentinel.DEFAULT
            ))

        stmts = replaced_imports
        if new_imports:
            stmts.append(updated_node.with_changes(names=new_imports))
        
        return cst.FlattenSentinel(stmts)

    def leave_ImportFrom(
        self, original_node: cst.ImportFrom, updated_node: cst.ImportFrom
    ) -> Union[cst.BaseSmallStatement, cst.RemovalSentinel]:
        if updated_node.module is None:
            return updated_node
        if updated_node.relative:
            return updated_node
        new_import_name = RemoveTypehint.REPLACE_MAPPING.get(str(updated_node.module.value))
        if new_import_name is not None:
            out = updated_node.with_deep_changes(
                updated_node.module, value=new_import_name
            ).with_changes(
                relative = [cst.Dot() for _ in range(self._relative_dots)],
            )
            return out
        else:
            if updated_node.module.value == '__future__':
                if isinstance(updated_node.names, cst.ImportStar):
                    return updated_node
                else:
                    names = updated_node.names
                    names = tuple(filter(lambda x: x.name.value != 'annotations', names))
                    if names:
                        return updated_node.with_changes(names=names)
                    else:
                        return cst.RemovalSentinel.REMOVE
            else:
                return updated_node

    # ==========================================
    # 删除 Generic[T]，BaseClass[T] 之类的表达式的 `[T]`
    
    def leave_Subscript(self, original_node: cst.Subscript, updated_node: cst.Subscript) -> cst.BaseExpression:
        # 以 pytypes 语言描述
        # 条件：
        # 1. 数据类型是 GenericType
        # 2. base_type == ClassType(builtin.type)
        # 3. parameters is not empty, is not None
        # 4. for all parameter in parameters
        # 5.     parameter 是 GenericType
        # 动作：
        #        parameter.parameters=(AnythingType(), )

        # 以 libcst 语言描述
        """
        Subscript(
          value=Name(id='Generic', ctx=Load()),
          slice=Name(id='T', ctx=Load()),
          ctx=Load()) --->
        Name(id='B', ctx=Load())
        """

        if not original_node.value:
            return updated_node

        cst_position = self.get_metadata(cstmeta.PositionProvider, original_node.value)
        if not cst_position:
            return updated_node

        position = expression_type.CodePosition(
            cst_position.start.line, cst_position.start.column, 
            cst_position.end.line, cst_position.end.column
        )


        pytype_type = self._type_info.get(position, None)
        if not isinstance(pytype_type, pytd.GenericType):
            return updated_node

        if not isinstance(pytype_type.base_type, pytd.ClassType):
            return updated_node

        if pytype_type.base_type.name != 'builtins.type':
            return updated_node

        return updated_node.value

    def leave_ClassDef(self, original_node: cst.ClassDef, updated_node: cst.ClassDef) -> cst.ClassDef:
        assert(len(original_node.bases) == len(updated_node.bases))
        
        new_bases = []
        for base, new_base in zip(original_node.bases, updated_node.bases):
            cst_position = self.get_metadata(cstmeta.PositionProvider, base.value)
            if not cst_position:
                new_bases.append(new_base)
                continue
            position = expression_type.CodePosition(
                cst_position.start.line, cst_position.start.column, 
                cst_position.end.line, cst_position.end.column
            )
            pytype_type = self._type_info.get(position, None)
  
            if (not pytype_type or 
                not isinstance(pytype_type, pytd.GenericType) or 
                not isinstance(pytype_type.base_type, pytd.ClassType) or 
                not pytype_type.base_type.name == 'builtins.type' or 
                not pytype_type.parameters):

                new_bases.append(new_base)
                continue

            real_type = pytype_type.parameters[0]
            if not real_type.name == 'typing.Generic':
                new_bases.append(new_base)
                continue 
        
        return updated_node.with_changes(bases=new_bases)

# call after RemoveTypehint
class RemoveName(cst.CSTTransformer):
    REMOVAL_PREFIX = '__cskip_'

    def leave_Assign(self, original_node, updated_node: cst.Assign):
        targets = list(updated_node.targets)
        new_targets = []
        for target in targets:
            if isinstance(target.target, cst.Name):
                if target.target.value.startswith(RemoveName.REMOVAL_PREFIX):
                    continue
            new_targets.append(target)
        
        if len(new_targets) == 0:
            return cst.Expr(value=updated_node.value)
        else:
            return updated_node.with_changes(targets=tuple(new_targets))

    def leave_ClassDef(self, original_node, updated_node: cst.ClassDef):
        if updated_node.name.value.startswith(RemoveName.REMOVAL_PREFIX):
            return cst.RemovalSentinel.REMOVE
    
        bases = list(updated_node.bases)
        new_bases = []
        for base in bases:
            if isinstance(base.value, cst.Name):
                if base.value.value.startswith(RemoveName.REMOVAL_PREFIX):
                    continue
            new_bases.append(base)
        return updated_node.with_changes(bases=tuple(new_bases))



def pretty_code(module: cst.Module) -> str:
    return module.code


def get_relative_dots(code_path: str, directory: str) -> int:
    relpath = os.path.relpath(os.path.abspath(code_path), os.path.abspath(directory))
    n_dots = len(relpath.replace('\\', '/').split('/'))
    return n_dots


def apply_libcst_change(code: str, code_path: str, module_directory: str) -> str:

    relative_dots = get_relative_dots(code_path, module_directory)
    types = expression_type.get_expression_types(code)

    cst_tree: Any = cst.parse_module(code)

    # 因为要用 pytype 的关系，需要保持语法树和源代码一致
    # 当然。。codegen + parse 都走一遍也不是不可以了。。
    cst_tree = cst.MetadataWrapper(cst_tree)
    cst_tree = cst_tree.visit(RemoveTypehint(relative_dots=relative_dots, type_info=types))
    cst_tree = cst_tree.visit(AddHeader())
    cst_tree = cst_tree.visit(AddImports())
    cst_tree = cst_tree.visit(Annotate())
    cst_tree = cst_tree.visit(RemoveName())

    target_code = pretty_code(cst_tree)

    return target_code


def apply_lib3to2_change(code: str) -> str:
    code = code + '\n'
    avail_fixes = set(refactor.get_fixers_from_package('lib3to2.fixes'))
    avail_fixes = avail_fixes.difference([
        'lib3to2.fixes.fix_printfunction',
        'lib3to2.fixes.fix_print',
        'lib3to2.fixes.fix_absimport',
        'lib3to2.fixed.fix_annotations'
    ])
    rt = refactor.RefactoringTool(sorted(avail_fixes), None, sorted(avail_fixes))
    tree = rt.refactor_string(code, '<code>')
    return str(tree)


class BASE64_CONSTS:
    PY_TYPING = 'ZGVmIF9mKCk6CiAgICBjbGFzcyBfYyhvYmplY3QpOgogICAgICAgIGRlZiBfX2luaXRfXyhzZWxmLCAqYXJncywgKiprd2FyZ3MpOgogICAgICAgICAgICBwYXNzCiAgICAgICAgZGVmIF9fY2FsbF9fKHNlbGYsICphcmdzLCAqKmt3YXJncyk6CiAgICAgICAgICAgIHJldHVybiBfYwogICAgICAgIGRlZiBfX2dldGl0ZW1fXyhzZWxmLCAqYXJncywgKiprd2FyZ3MpOgogICAgICAgICAgICByZXR1cm4gX2MKICAgIHJldHVybiBfYwpBbm5vdGF0ZWQ9X2YoKQpBbnk9X2YoKQpDYWxsYWJsZT1fZigpCkNsYXNzVmFyPV9mKCkKQ29uY2F0ZW5hdGU9X2YoKQpGaW5hbD1fZigpCkZvcndhcmRSZWY9X2YoKQpHZW5lcmljPV9mKCkKTGl0ZXJhbD1fZigpCk9wdGlvbmFsPV9mKCkKUGFyYW1TcGVjPV9mKCkKUHJvdG9jb2w9X2YoKQpUdXBsZT1fZigpClR5cGU9X2YoKQpUeXBlVmFyPV9mKCkKVW5pb249X2YoKQpBYnN0cmFjdFNldD1fZigpCkJ5dGVTdHJpbmc9X2YoKQpDb250YWluZXI9X2YoKQpDb250ZXh0TWFuYWdlcj1fZigpCkhhc2hhYmxlPV9mKCkKSXRlbXNWaWV3PV9mKCkKSXRlcmFibGU9X2YoKQpJdGVyYXRvcj1fZigpCktleXNWaWV3PV9mKCkKTWFwcGluZz1fZigpCk1hcHBpbmdWaWV3PV9mKCkKTXV0YWJsZU1hcHBpbmc9X2YoKQpNdXRhYmxlU2VxdWVuY2U9X2YoKQpNdXRhYmxlU2V0PV9mKCkKU2VxdWVuY2U9X2YoKQpTaXplZD1fZigpClZhbHVlc1ZpZXc9X2YoKQpBd2FpdGFibGU9X2YoKQpBc3luY0l0ZXJhdG9yPV9mKCkKQXN5bmNJdGVyYWJsZT1fZigpCkNvcm91dGluZT1fZigpCkNvbGxlY3Rpb249X2YoKQpBc3luY0dlbmVyYXRvcj1fZigpCkFzeW5jQ29udGV4dE1hbmVyPV9mKCkKUmV2ZXJzaWJsZT1fZigpClN1cHBvcnRzQWJzPV9mKCkKU3VwcG9ydHNCeXRlcz1fZigpClN1cHBvcnRzQ29tcGxleD1fZigpClN1cHBvcnRzRmxvYXQ9X2YoKQpTdXBwb3J0c0luZGV4PV9mKCkKU3VwcG9ydHNJbnQ9X2YoKQpTdXBwb3J0c1JvdW5kPV9mKCkKQ2hhaW5NYXA9X2YoKQpDb3VudGVyPV9mKCkKRGVxdWU9X2YoKQpEaWN0PV9mKCkKRGVmYXVsdERpY3Q9X2YoKQpMaXN0PV9mKCkKT3JkZXJlZERpY3Q9X2YoKQpTZXQ9X2YoKQpGcm96ZW5TZXQ9X2YoKQpOYW1lZFR1cGxlPV9mKCkKVHlwZWREaWN0PV9mKCkKR2VuZXJhdG9yPV9mKCkKQmluYXJ5SU89X2YoKQpJTz1fZigpCk1hdGNoPV9mKCkKUGF0dGVybj1fZigpClRleHRJTz1fZigpCkFueVN0cj1fZigpCmNhc3Q9X2YoKQpmaW5hbD1fZigpCmdldF9hcmdzPV9mKCkKZ2V0X29yaWdpbj1fZigpCmdldF90eXBlX2hpbnRzPV9mKCkKaXNfdHlwZWRkaWN0PV9mKCkKTmV3VHlwZT1fZigpCm5vX3R5cGVfY2hlY2s9X2YoKQpub190eXBlX2NoZWNrX2RvcmF0bz1fZigpCk5vUmV0dXJuPV9mKCkKb3ZlcmxvYWQ9X2YoKQpQYXJhbVNwZWNBcmdzPV9mKCkKUGFyYW1TcGVjS3dhcmdzPV9mKCkKcnVudGltZV9jaGVja2FiPV9mKCkKVGV4dD1fZigpClRZUEVfQ0hFQ0tJTkc9X2YoKQpUeXBlQWxpYXM9X2YoKQpUeXBlR3VhcmQ9X2YoKQ=='
    PY_TYPING_EXTENSION = 'ZGVmIF9mKCk6CiAgICBjbGFzcyBfYyhvYmplY3QpOgogICAgICAgIGRlZiBfX2luaXRfXyhzZWxmLCAqYXJncywgKiprd2FyZ3MpOgogICAgICAgICAgICBwYXNzCiAgICAgICAgZGVmIF9fY2FsbF9fKHNlbGYsICphcmdzLCAqKmt3YXJncyk6CiAgICAgICAgICAgIHJldHVybiBfYwogICAgICAgIGRlZiBfX2dldGl0ZW1fXyhzZWxmLCAqYXJncywgKiprd2FyZ3MpOgogICAgICAgICAgICByZXR1cm4gX2MKICAgIHJldHVybiBfYwpDbGFzc1Zhcj1fZigpCkNvbmNhdGVuYXRlPV9mKCkKRmluYWw9X2YoKQpMaXRlcmFsU3RyaW5nPV9mKCkKUGFyYW1TcGVjPV9mKCkKUGFyYW1TcGVjQXJncz1fZigpClBhcmFtU3BlY0t3YXJncz1fZigpClNlbGY9X2YoKQpUeXBlPV9mKCkKVHlwZVZhclR1cGxlPV9mKCkKVW5wYWNrPV9mKCkKQXdhaXRhYmxlPV9mKCkKQXN5bmNJdGVyYXRvcj1fZigpCkFzeW5jSXRlcmFibGU9X2YoKQpDb3JvdXRpbmU9X2YoKQpBc3luY0dlbmVyYXRvcj1fZigpCkFzeW5jQ29udGV4dE1hbj1fZigpCkNoYWluTWFwPV9mKCkKQ29udGV4dE1hbmFnZXI9X2YoKQpDb3VudGVyPV9mKCkKRGVxdWU9X2YoKQpEZWZhdWx0RGljdD1fZigpCk9yZGVyZWREaWN0PV9mKCkKVHlwZWREaWN0PV9mKCkKU3VwcG9ydHNJbmRleD1fZigpCkFubm90YXRlZD1fZigpCmFzc2VydF9uZXZlcj1fZigpCmFzc2VydF90eXBlPV9mKCkKY2xlYXJfb3ZlcmxvYWRzPV9mKCkKZGF0YWNsYXNzX3RyYW5zPV9mKCkKZ2V0X292ZXJsb2Fkcz1fZigpCmZpbmFsPV9mKCkKZ2V0X2FyZ3M9X2YoKQpnZXRfb3JpZ2luPV9mKCkKZ2V0X3R5cGVfaGludHM9X2YoKQpJbnRWYXI9X2YoKQppc190eXBlZGRpY3Q9X2YoKQpMaXRlcmFsPV9mKCkKTmV3VHlwZT1fZigpCm92ZXJsb2FkPV9mKCkKUHJvdG9jb2w9X2YoKQpyZXZlYWxfdHlwZT1fZigpCnJ1bnRpbWU9X2YoKQpydW50aW1lX2NoZWNrYWI9X2YoKQpUZXh0PV9mKCkKVHlwZUFsaWFzPV9mKCkKVHlwZUd1YXJkPV9mKCkKVFlQRV9DSEVDS0lORz1fZigpCk5ldmVyPV9mKCkKTm9SZXR1cm49X2YoKQpSZXF1aXJlZD1fZigpCk5vdFJlcXVpcmVkPV9mKCk='


