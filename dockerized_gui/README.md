### Prerequisites

Install "Xephyr" nested x server, "bc" utility for some arithmetics, "sshpass" and "xclip":

```
sudo apt install xserver-xephyr bc sshpass xclip
```

Also note that make script will fetch external repository with clipboard tracking utility.

### HowTo

Build image: `./make.sh`.
Run container and attach Xephyr window by `./run.sh` command.

Once container is created it runs infinitely untill reboot or stopped explicitly, but rvery run creates separate X context and runns application in this context, so GUI application state is not between different runs.

### Known issues

- Sometimes while using mouse scrolling wheel, mouse stop responding. Need to press control and use mouse on top of Xephyr window.
- Running windows are not shown on bottom panel
- clipboard is copied in ping pong several times. This is not expected.
