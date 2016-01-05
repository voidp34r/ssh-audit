# ssh-audit
**ssh-audit** is a tool for ssh server auditing.  

## Features
- grab banner, detect ssh1 protocol and zlib compression;
- gather key-exchange, host-key, encryption and message authentication code algorithms;
- output algorithm information (available since, removed/disabled, unsafe/weak/legacy, etc);
- historical information from OpenSSH and Dropbear SSH;
- no dependencies, compatible with python2 and python3;

## Usage
```
usage: ssh-audit.py [-nv] host[:port]

   -v  verbose
   -n  disable colors
```
Verbose flag will fill each row, i.e, not leave blanks, for easier usage with _batch_ scripts or with manual grepping.

### example
![screenshot](https://cloud.githubusercontent.com/assets/7356025/12120117/fa0ef9f8-b3d7-11e5-9a1b-f410b5217529.png)  

## ChangeLog
### v1.0.20160105
 - multiple additional warnings
 - support for none algorithm
 - better compression handling  
 - ensure reading enough data (fixes few Linux SSH)  

### v1.0.20151230
 - Dropbear SSH support  

### v1.0.20151223
 - initial version  
