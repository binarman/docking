Container and scripts are based on tutorial from https://www.digitalocean.com/community/tutorials/how-to-set-up-and-configure-an-openvpn-server-on-ubuntu-20-04-ru

## Scripts

[make.sh](https://github.com/binarman/docking/tree/main/openvpn/server_deployment/make.sh): build an image
[run.sh](https://github.com/binarman/docking/tree/main/openvpn/server_deployment/run.sh): run a container, which will restart on host restart

## FAQ

### Server status

option one

``` bash
tail -f /var/log/openvpn/openvpn-status.log
```

option two

``` bash
service openvpn* status
```

option three

``` bash
systemctl status openvpn*
```

### Get server port

``` bash
cat /etc/openvpn/server/server.conf  | grep port
```

## Known problems

### 1

Connection is established, can connect via ssh, but no pages working in browser and nothing is pinging.
- try to add openvpn port into firewall exceptions and start firewall:

``` bash
sudo ufw allow 443
sudo ufw enable  
```

### 2

Can not connect at all.
- Check that server IP address is correct in config, for some reason it could change.