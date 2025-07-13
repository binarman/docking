Container to build linux kernel

- mainline: https://cdn.kernel.org/pub/linux/kernel/
- with patches for orangepi: https://codeberg.org/megi/linux

# Build for arm64

```
export ARCH=arm64
export CROSS_COMPILE=aarch64-linux-gnu-
make orangepi_defconfig
make Image
```
