### Prerequisites

Install "Xephyr" nested x server, "bc" utility for some arithmetics and "sshpass":

```
sudo apt install xserver-xephyr bc sshpass
```

### HowTo

Build image: `./make.sh`.
Run container and attach Xephyr window by `./run.sh` command.

Once container is created it runs infinitely untill reboot or stopped explicitly, but rvery run creates separate X context and runns application in this context, so GUI application state is not between different runs.

### TODO

- forward sound to host
- share clipboard between host and container
