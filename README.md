# givenergy-commerical-modbus-tcp
Read data via Modbus TCP from GivEnergy Commercial AIO battery 

Tested on GivEnergy Commercial All-In-One 69kWh using a RaspberryPi

![](battery-photo.jpg)



# Connections

- Modbus TCP on NET1 (NET2 is used for cloud connection) 
- NET1 Fixed IP of: `192.168.2.230`
- Default slave address `0x11`

Connect NET1 directly to Pi's Etherent connection, then setup the Pi to have an address in the same range: 

```
sudo nmcli connection modify "Wired connection 1" ipv4.addresses 192.168.2.10/24
sudo nmcli connection modify "Wired connection 1" ipv4.method manual
sudo nmcli connection up "Wired connection 1"
```
Check:

```
eth0: flags=4163<UP,BROADCAST,RUNNING,MULTICAST>  mtu 1500
        inet 192.168.2.10  netmask 255.255.255.0  broadcast 192.168.2.255
        inet6 fe80::b092:5ff2:5dbc:c4b3  prefixlen 64  scopeid 0x20<link>
        ether xxxxxxxxxx  txqueuelen 1000  (Ethernet)
        RX packets 378  bytes 49718 (48.5 KiB)
        RX errors 0  dropped 0  overruns 0  frame 0
        TX packets 424  bytes 33120 (32.3 KiB)
        TX errors 0  dropped 0 overruns 0  carrier 0  collisions 0
```
Test for ping: 
```
ping 192.168.2.230
PING 192.168.2.230 (192.168.2.230) 56(84) bytes of data.
64 bytes from 192.168.2.230: icmp_seq=1 ttl=64 time=0.680 ms
```

![](net1-photo.jpg)

# Install 

```
sudo cp givenergy-emoncms.env.example /etc/default/givenergy-emoncms
sudo chmod 600 /etc/default/givenergy-emoncms
sudo cp givenergy-emoncms.service /etc/systemd/system
sudo systemctl daemon-reload
sudo systemctl enable --now givenergy-emoncms
journalctl -u givenergy-emoncms -f
```

![](emoncms-inputs.png)