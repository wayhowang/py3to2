from collections import namedtuple
import pytype
import pytype.io
import pytype.config
import pytype.pytd.pytd as pytd
import pytype.tools.traces
import pytype.tools.traces.traces
import pytype.pytd.pytd_utils
import pytype.tools.annotate_ast.annotate_ast as aast
import ast
from typing import *


def full_annotate_source(source, ast_module, pytype_options):
    with pytype.io.wrap_pytype_exceptions(Exception, filename=pytype_options.input):
        source_code = pytype.tools.traces.traces.trace(source, pytype_options)

    module = ast_module.parse(source, pytype_options.input)
    visitor = FullAnnotateAstVisitor(source_code, ast_module)
    visitor.visit(module)
    return module


class FullAnnotateAstVisitor(pytype.tools.traces.traces.MatchAstVisitor):

    def _maybe_annotate(self, node):
        """Annotates a node."""
        try:
            ops = self.match(node)
        except NotImplementedError:
            return
        # For lack of a better option, take the first one.
        unused_loc, entry = next(iter(ops), (None, None))
        self._maybe_set_type(node, entry)

    def _maybe_set_type(self, node, trace):
        """Sets type information on the node, if there is any to set."""
        if not trace:
            return
        node.resolved_type = trace.types[-1]
        node.resolved_annotation = pytype.pytd.pytd_utils.Print(trace.types[-1])

    def _call_visitor(self, node):
        self._maybe_annotate(node)


CodePosition = namedtuple('CodePosition', ['lineno', 'col_offset', 'end_lineno', 'end_col_offset'])


def generate_annotation_map(module: ast.AST) -> Dict[CodePosition, pytd.Type]:
    mapping: Dict[CodePosition, pytd.Type] = {}

    for node in ast.walk(module):
        resolved_type = getattr(node, 'resolved_type', None)
        if not resolved_type:
            continue
        if not isinstance(resolved_type, pytd.Type):
            continue
        pos = CodePosition(node.lineno, node.col_offset, node.end_lineno, node.end_col_offset)
        mapping[pos] = resolved_type

    return mapping


def get_expression_types(source: str, config=None) -> Dict[CodePosition, pytd.Type]:
    # 目前不会分析 relative import... 
    # 更改 Options 或可 ~

    if config is None:
        config = pytype.config.Options.create()
    
    module = full_annotate_source(source, ast, pytype.config.Options.create())
    return generate_annotation_map(module)

