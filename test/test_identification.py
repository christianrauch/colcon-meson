# Copyright 2024 Christian Rauch
# Licensed under the Apache License, Version 2.0

import os

from colcon_core.package_descriptor import PackageDescriptor
from colcon_meson.identification import MesonPackageIdentification


test_project_path = os.path.join(
    os.path.dirname(os.path.realpath(__file__)),
    'meson_test_project',
)


def test_capital_case():
    mpi = MesonPackageIdentification()
    desc = PackageDescriptor(test_project_path)
    mpi.identify(desc)
    assert desc.type == 'meson'
    assert desc.name == 'meson_test_project'
