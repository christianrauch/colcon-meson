# Copyright 2024 Christian Rauch
# Licensed under the Apache License, Version 2.0

import os
import typing

from mesonbuild import environment
from mesonbuild import mesonlib
from mesonbuild.interpreterbase.interpreterbase import InterpreterBase
from mesonbuild.interpreterbase.baseobjects import mparser, InterpreterObject
from mesonbuild.interpreter import primitives

from colcon_core.logging import colcon_logger
from colcon_core.package_identification import PackageIdentificationExtensionPoint

logger = colcon_logger.getChild(__name__)


class CustomInterpreter(InterpreterBase):
    def __init__(self, source_root: str, subdir: str, subproject: str):
        super().__init__(source_root, subdir, subproject)

        self.holder_map.update({
            list: primitives.ArrayHolder,
            dict: primitives.DictHolder,
            int: primitives.IntegerHolder,
            bool: primitives.BooleanHolder,
            str: primitives.StringHolder,
        })

        self.environment = environment

        self.data = dict()
        self.data["dependencies"] = set()

    def evaluate_statement(self, cur: mparser.BaseNode) -> typing.Optional[InterpreterObject]:
        if isinstance(cur, mparser.FunctionNode):
            return self.function_call(cur)
        elif isinstance(cur, mparser.AssignmentNode):
            self.assignment(cur)
        elif isinstance(cur, mparser.StringNode):
            return self._holderify(cur.value)
        elif isinstance(cur, mparser.ArrayNode):
            return self.evaluate_arraystatement(cur)
        return None

    def function_call(self, node: mparser.FunctionNode) -> typing.Optional[InterpreterObject]:
        node_func_name = f"{type(node.func_name).__module__}.{type(node.func_name).__qualname__}"
        if node_func_name == "str":
            # meson <= 1.2
            func_name = node.func_name
        elif node_func_name == "mesonbuild.mparser.IdNode":
            # meson >= 1.3
            func_name = node.func_name.value
        else:
            raise AttributeError("Cannot determine meson project name.")

        assert type(func_name) == str

        reduced_pos = [self.evaluate_statement(arg) for arg in node.args.arguments]
        reduced_pos = list(filter(None, reduced_pos))
        args = self._unholder_args(reduced_pos, {})[0]

        if func_name == "project":
            self.data["name"] = args[0]
        elif func_name == "dependency":
            self.data["dependencies"].update(args)
        elif func_name == "subdir":
            subpath = os.path.join(self.source_root, args[0])
            parser = CustomInterpreter(subpath, "", "")
            subdata = parser.parse()
            for k in subdata.keys():
                self.data[k].update(subdata[k])
        return None

    def assignment(self, node: mparser.AssignmentNode) -> None:
        self.evaluate_statement(node.value)
        return None

    def evaluate_arraystatement(self, cur: mparser.ArrayNode) -> InterpreterObject:
        arguments = [self.evaluate_statement(arg) for arg in cur.args.arguments]
        arguments = list(filter(None, arguments))
        return self._holderify(self._unholder_args(arguments, {})[0])

    def parse(self) -> dict:
        try:
            self.load_root_meson_file()
        except mesonlib.MesonException:
            return dict()

        self.evaluate_codeblock(self.ast)
        return self.data


class MesonPackageIdentification(PackageIdentificationExtensionPoint):
    def __init__(self):
        super().__init__()

    def identify(self, metadata):
        parser = CustomInterpreter(metadata.path, "", "")
        data = parser.parse()

        if not data:
            return

        metadata.type = 'meson'

        if metadata.name is None:
            metadata.name = data["name"]

        logger.info("'%s' dependencies: %s", metadata.name, data['dependencies'])

        metadata.dependencies['build'].update(data['dependencies'])
        metadata.dependencies['run'].update(data['dependencies'])
