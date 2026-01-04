#!/bin/bash
MAC="F4:4E:FC:B6:65:D4" # SoundCore 2のMACアドレス
connected=$(bluetoothctl info "$MAC" | grep "Connected: yes")

if [ -z "$connected" ]; then
    echo "Connecting to SoundCore 2..."
    bluetoothctl connect "$MAC"
fi