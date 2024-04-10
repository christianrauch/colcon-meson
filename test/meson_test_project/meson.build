project(
  'meson_test_project', 'c',
  version : '0.1',
  default_options : ['warning_level=3']
)

exe = executable(
  'meson_test_project',
  'meson_test_project.c',
  install : true
)

test('basic', exe)
