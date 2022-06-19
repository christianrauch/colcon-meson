# colcon-meson

A colcon extension for building [Meson](https://mesonbuild.com) packages.

## Passing Meson arguments

Arguments can be passed to `meson setup` via `--meson-args`:
1. set build options defined in `meson_options.txt`:
    ```sh
    colcon build --packages-select $PACKAGE --meson-args \
        -D$ARG1=$PARAM1 \
        -D$ARG2=$PARAM2
    ```
2. set the build type:
    ```sh
    colcon build --packages-select $PACKAGE --meson-args \
        --buildtype=debugoptimized
    ```

See `meson setup -h` for a detailed list of arguments.
