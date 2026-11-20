# scrcpy-server

`scrcpy-server-v5.0.1` : serveur de [scrcpy](https://github.com/Genymobile/scrcpy) (Genymobile,
licence Apache 2.0, voir `LICENSE`), tel que publié avec la version 5.0.1 :
<https://github.com/Genymobile/scrcpy/releases/tag/v5.0.1>

SHA-256 : `764eb6f79811d5211fe9df341120882ba9994c7a61b897d7bf3fb662e53bc536`
(celui de `SHA256SUMS.txt` de la publication).

Boostache le pousse sur le téléphone et le lance par adb (section Smartphone, `phone.py`).
Le protocole entre le client et le serveur change d'une version à l'autre : en changer
demande de mettre à jour `SCRCPY_VERSION` et de relire le protocole dans `phone.py`.
