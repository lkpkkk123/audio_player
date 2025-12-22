cd ..
rsync -avz --exclude='venv' --exclude='.git' audio_player/ root@192.168.8.100:/opt/audio_player/