[Unit]
Description=METAR Station Weather daemon
After=multi-user.target

[Service]
User=$METARSTATION_USER
Restart=always
ExecStart=/opt/metarstation-daemon/weather-daemon.py -c /etc/metarstation/config.toml
StandardError=journal

[Install]
WantedBy=default.target
