#rsync -avz media/ root@192.168.8.100:/opt/audio_player/media/
rsync -avz public/ root@192.168.9.29:/opt/audio_player/public/
rsync -avz server.py root@192.168.9.29:/opt/audio_player/server.py
#rsync -avz install_bin.tgz root@192.168.8.100:/opt/audio_player/install_bin.tgz
rsync -avz service/ root@192.168.9.29:/opt/audio_player/service/
