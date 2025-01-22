### Prerequisites

Install "Xephyr" nested x server, "bc" utility for some arithmetics and "sshpass":

```
sudo apt install xserver-xephyr bc sshpass
```

### HowTo

To build image run `./make.sh` command

Run container and attach Xephyr window by `./run.sh` command

### TODO

- automate installation of citrix?
- run container as a daemon
- desktop icons?
- list of running windows?
- forward sound to host
- is it possible to not drop x session and kill all applications?
- configurable password
