#!/bin/bash

tar -xf p1.tar -C /usr/lib/
tar -xf p2.tar -C /usr/lib/
tar -xf p3.tar -C /usr/local/lib/
cd deb && dpkg -i *.deb