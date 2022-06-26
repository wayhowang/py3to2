from typing import *
import libcst as cst
import libcst.metadata as cstmeta
import lib2to3.refactor as refactor # yes, lib2to3. It calls fixes defined in lib3to2
import sys
import argparse
import os
import base64


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
        return updated_node.with_changes(header = (stmt, ) + updated_node.header)

class AddImports(cst.CSTTransformer):
    def leave_Module(
        self, original_node: cst.Module, updated_node: cst.Module
    ):
        stmt = cst.parse_statement('from __future__ import absolute_import, division, print_function, unicode_literals')
        return updated_node.with_changes(body = (stmt, ) + updated_node.body)

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

    def leave_ImportAlias(self, original_node: cst.ImportAlias, updated_node: cst.ImportAlias) -> cst.ImportAlias:
        new_import_name = RemoveTypehint.REPLACE_MAPPING.get(updated_node.name.value)
        if new_import_name is not None:
            return updated_node.with_deep_changes(updated_node.name, value=new_import_name)
        else:
            return updated_node

    def leave_ImportFrom(
        self, original_node: cst.ImportFrom, updated_node: cst.ImportFrom
    ) -> Union[cst.BaseSmallStatement, cst.RemovalSentinel]:
        new_import_name = RemoveTypehint.REPLACE_MAPPING.get(updated_node.module.value)
        if new_import_name is not None:
            return updated_node.with_deep_changes(updated_node.module, value=new_import_name)
        else:
            return updated_node


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


def apply_libcst_change(code: str) -> str:
    # libcst 提供的 FullyQualifiedNameProvider 不知道怎麽 import 系統庫~ 文檔也不多
    # 内部是通過跨進程調用的方式搞，似乎也不太靠譜，容易出問題
    # 所以先用 jedi 了

    cst_tree = cst.parse_module(code)

    # 因为要用 JEDI 的关系，需要保持语法树和源代码一致
    # 当然。。codegen + parse 都走一遍也不是不可以了。。
    # cst_tree = cst.MetadataWrapper(cst_tree)
    cst_tree = cst_tree.visit(RemoveTypehint())    
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
    PY_TYPING = 'ZGVmIF9mKCk6CiAgICBjbGFzcyBfYyhvYmplY3QpOgogICAgICAgIGRlZiBfX2luaXRfXyhzZWxmLCAqYXJncywgKiprd2FyZ3MpOgogICAgICAgICAgICBwYXNzCiAgICAgICAgZGVmIF9fY2FsbF9fKHNlbGYsICphcmdzLCAqKmt3YXJncyk6CiAgICAgICAgICAgIHJldHVybiBfYwogICAgICAgIGRlZiBfX2dldGl0ZW1fXyhzZWxmLCAqYXJncywgKiprd2FyZ3MpOgogICAgICAgICAgICByZXR1cm4gX2MKICAgIHJldHVybiBfYygpCgoKQW5ub3RhdGVkPV9mKCkKQW55PV9mKCkKQ2FsbGFibGU9X2YoKQpDbGFzc1Zhcj1fZigpCkNvbmNhdGVuYXRlPV9mKCkKRmluYWw9X2YoKQpGb3J3YXJkUmVmPV9mKCkKR2VuZXJpYz1fZigpCkxpdGVyYWw9X2YoKQpPcHRpb25hbD1fZigpClBhcmFtU3BlYz1fZigpClByb3RvY29sPV9mKCkKVHVwbGU9X2YoKQpUeXBlPV9mKCkKVHlwZVZhcj1fZigpClVuaW9uPV9mKCkKQWJzdHJhY3RTZXQ9X2YoKQpCeXRlU3RyaW5nPV9mKCkKQ29udGFpbmVyPV9mKCkKQ29udGV4dE1hbmFnZXI9X2YoKQpIYXNoYWJsZT1fZigpCkl0ZW1zVmlldz1fZigpCkl0ZXJhYmxlPV9mKCkKSXRlcmF0b3I9X2YoKQpLZXlzVmlldz1fZigpCk1hcHBpbmc9X2YoKQpNYXBwaW5nVmlldz1fZigpCk11dGFibGVNYXBwaW5nPV9mKCkKTXV0YWJsZVNlcXVlbmNlPV9mKCkKTXV0YWJsZVNldD1fZigpClNlcXVlbmNlPV9mKCkKU2l6ZWQ9X2YoKQpWYWx1ZXNWaWV3PV9mKCkKQXdhaXRhYmxlPV9mKCkKQXN5bmNJdGVyYXRvcj1fZigpCkFzeW5jSXRlcmFibGU9X2YoKQpDb3JvdXRpbmU9X2YoKQpDb2xsZWN0aW9uPV9mKCkKQXN5bmNHZW5lcmF0b3I9X2YoKQpBc3luY0NvbnRleHRNYW5lcj1fZigpClJldmVyc2libGU9X2YoKQpTdXBwb3J0c0Ficz1fZigpClN1cHBvcnRzQnl0ZXM9X2YoKQpTdXBwb3J0c0NvbXBsZXg9X2YoKQpTdXBwb3J0c0Zsb2F0PV9mKCkKU3VwcG9ydHNJbmRleD1fZigpClN1cHBvcnRzSW50PV9mKCkKU3VwcG9ydHNSb3VuZD1fZigpCkNoYWluTWFwPV9mKCkKQ291bnRlcj1fZigpCkRlcXVlPV9mKCkKRGljdD1fZigpCkRlZmF1bHREaWN0PV9mKCkKTGlzdD1fZigpCk9yZGVyZWREaWN0PV9mKCkKU2V0PV9mKCkKRnJvemVuU2V0PV9mKCkKTmFtZWRUdXBsZT1fZigpClR5cGVkRGljdD1fZigpCkdlbmVyYXRvcj1fZigpCkJpbmFyeUlPPV9mKCkKSU89X2YoKQpNYXRjaD1fZigpClBhdHRlcm49X2YoKQpUZXh0SU89X2YoKQpBbnlTdHI9X2YoKQpjYXN0PV9mKCkKZmluYWw9X2YoKQpnZXRfYXJncz1fZigpCmdldF9vcmlnaW49X2YoKQpnZXRfdHlwZV9oaW50cz1fZigpCmlzX3R5cGVkZGljdD1fZigpCk5ld1R5cGU9X2YoKQpub190eXBlX2NoZWNrPV9mKCkKbm9fdHlwZV9jaGVja19kb3JhdG89X2YoKQpOb1JldHVybj1fZigpCm92ZXJsb2FkPV9mKCkKUGFyYW1TcGVjQXJncz1fZigpClBhcmFtU3BlY0t3YXJncz1fZigpCnJ1bnRpbWVfY2hlY2thYj1fZigpClRleHQ9X2YoKQpUWVBFX0NIRUNLSU5HPV9mKCkKVHlwZUFsaWFzPV9mKCkKVHlwZUd1YXJkPV9mKCk='
    PY_TYPING_EXTENSION = 'ZGVmIF9mKCk6DQogICAgY2xhc3MgX2Mob2JqZWN0KToNCiAgICAgICAgZGVmIF9faW5pdF9fKHNlbGYsICphcmdzLCAqKmt3YXJncyk6DQogICAgICAgICAgICBwYXNzDQogICAgICAgIGRlZiBfX2NhbGxfXyhzZWxmLCAqYXJncywgKiprd2FyZ3MpOg0KICAgICAgICAgICAgcmV0dXJuIF9jDQogICAgICAgIGRlZiBfX2dldGl0ZW1fXyhzZWxmLCAqYXJncywgKiprd2FyZ3MpOg0KICAgICAgICAgICAgcmV0dXJuIF9jDQogICAgcmV0dXJuIF9jKCkNCkNsYXNzVmFyPV9mKCkNCkNvbmNhdGVuYXRlPV9mKCkNCkZpbmFsPV9mKCkNCkxpdGVyYWxTdHJpbmc9X2YoKQ0KUGFyYW1TcGVjPV9mKCkNClBhcmFtU3BlY0FyZ3M9X2YoKQ0KUGFyYW1TcGVjS3dhcmdzPV9mKCkNClNlbGY9X2YoKQ0KVHlwZT1fZigpDQpUeXBlVmFyVHVwbGU9X2YoKQ0KVW5wYWNrPV9mKCkNCkF3YWl0YWJsZT1fZigpDQpBc3luY0l0ZXJhdG9yPV9mKCkNCkFzeW5jSXRlcmFibGU9X2YoKQ0KQ29yb3V0aW5lPV9mKCkNCkFzeW5jR2VuZXJhdG9yPV9mKCkNCkFzeW5jQ29udGV4dE1hbj1fZigpDQpDaGFpbk1hcD1fZigpDQpDb250ZXh0TWFuYWdlcj1fZigpDQpDb3VudGVyPV9mKCkNCkRlcXVlPV9mKCkNCkRlZmF1bHREaWN0PV9mKCkNCk9yZGVyZWREaWN0PV9mKCkNClR5cGVkRGljdD1fZigpDQpTdXBwb3J0c0luZGV4PV9mKCkNCkFubm90YXRlZD1fZigpDQphc3NlcnRfbmV2ZXI9X2YoKQ0KYXNzZXJ0X3R5cGU9X2YoKQ0KY2xlYXJfb3ZlcmxvYWRzPV9mKCkNCmRhdGFjbGFzc190cmFucz1fZigpDQpnZXRfb3ZlcmxvYWRzPV9mKCkNCmZpbmFsPV9mKCkNCmdldF9hcmdzPV9mKCkNCmdldF9vcmlnaW49X2YoKQ0KZ2V0X3R5cGVfaGludHM9X2YoKQ0KSW50VmFyPV9mKCkNCmlzX3R5cGVkZGljdD1fZigpDQpMaXRlcmFsPV9mKCkNCk5ld1R5cGU9X2YoKQ0Kb3ZlcmxvYWQ9X2YoKQ0KUHJvdG9jb2w9X2YoKQ0KcmV2ZWFsX3R5cGU9X2YoKQ0KcnVudGltZT1fZigpDQpydW50aW1lX2NoZWNrYWI9X2YoKQ0KVGV4dD1fZigpDQpUeXBlQWxpYXM9X2YoKQ0KVHlwZUd1YXJkPV9mKCkNClRZUEVfQ0hFQ0tJTkc9X2YoKQ0KTmV2ZXI9X2YoKQ0KTm9SZXR1cm49X2YoKQ0KUmVxdWlyZWQ9X2YoKQ0KTm90UmVxdWlyZWQ9X2YoKQ=='


