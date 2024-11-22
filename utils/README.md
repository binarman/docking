### Connect

To make it run without root password add following lines in `/etc/sudoers`:

```
%sudo      ALL=(root:root) NOPASSWD:/usr/bin/connect
```

and copy connect script to `/usr/bin/connect`
