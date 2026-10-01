# ☁️ Guía de Despliegue 24/7: Bot Cuantitativo en Oracle Cloud (Always Free Tier)

**Oracle Cloud Infrastructure (OCI) Always Free Tier** es la opción ideal para hospedar este bot de trading de forma **100% gratuita y permanente**. Evita que las interrupciones eléctricas locales, cortes de internet o apagar la PC interfieran con el monitoreo continuo del mercado.

---

## 🎁 1. Especificaciones Gratuitas de Oracle Cloud (Always Free)

Oracle regala permanentemente los siguientes recursos:

- **Instancias Ampere A1 (ARM)**: Hasta **4 OCPUs** y **24 GB de RAM** (puedes crear 1 VPS de 2 OCPUs + 12 GB RAM completamente gratis).
- **Almacenamiento**: **200 GB** de disco SSD.
- **Tráfico de Red**: **10 TB/mes** de ancho de banda gratuito.
- **Dirección IP Pública Estática**: Incluida sin costo.

---

## 🛠️ 2. Paso a Paso para la Instalación en la Nube

### Paso 1: Registro en Oracle Cloud
1. Entra a [oracle.com/cloud/free/](https://www.oracle.com/cloud/free/) y crea una cuenta.
2. Selecciona una **Región Principal (Home Region)** cercana (ej. *US East - Ashburn*).
3. Requiere una tarjeta de crédito/débito para verificar identidad (Oracle hace un cargo temporal de $1 USD y lo devuelve de inmediato).

---

### Paso 2: Crear la VPS (Instancia de Computo)
1. En el panel de Oracle Cloud, ve a **Compute** $\rightarrow$ **Instances** $\rightarrow$ **Create Instance**.
2. **Nombre**: `TradingBot-VPS`.
3. **Image and Shape**:
   - **Image**: `Ubuntu 22.04 LTS` o `Ubuntu 24.04 LTS`.
   - **Shape**: Selecciona `Ampere (ARM)` $\rightarrow$ `VM.Standard.A1.Flex` (Configura **2 OCPUs** y **12 GB de RAM**).
4. **Networking**: Deja la VCN por defecto con asignación de **IP pública**.
5. **Add SSH Keys**:
   - Selecciona *"Generate a key pair for me"* y descarga la **Clave Privada (`.key` / `.pem`)**. ¡Guárdala bien en tu PC!
6. Haz clic en **Create**. En 1 o 2 minutos tu VPS estará en estado **RUNNING** con una **Public IP** asignada.

---

### Paso 3: Conectarse por SSH a la VPS desde tu PC
Abre PowerShell en Windows y ejecuta (sustituyendo la ruta a tu clave y la IP de tu VPS):

```powershell
ssh -i "C:\Ruta\A\Tu\ssh-key-2026.key" ubuntu@<TU_IP_PUBLICA_ORACLE>
```

---

### Paso 4: Preparar el Entorno en Ubuntu Linux
Dentro de la VPS en la consola SSH, ejecuta los siguientes comandos:

```bash
# 1. Actualizar el sistema operativo
sudo apt update && sudo apt upgrade -y

# 2. Instalador de Python, Pip y Git
sudo apt install python3-pip python3-venv git -y

# 3. Crear directorio para el bot
mkdir -p ~/trading_bot && cd ~/trading_bot

# 4. Crear entorno virtual de Python
python3 -m venv venv
source venv/bin/activate

# 5. Instalar librerías cuantitativas requeridas
pip install --upgrade pip
pip install pandas numpy requests python-dotenv
```

---

### Paso 5: Transferir los Archivos del Bot a la VPS
Puedes subir los archivos del bot desde tu máquina local usando `scp` o un cliente gráfico como **WinSCP** o **FileZilla**:

#### Opción A: Copiar mediante comando `scp` desde PowerShell en tu PC:
```powershell
scp -i "C:\Ruta\A\Tu\ssh-key.key" -r "C:\Users\victo\.gemini\antigravity\scratch\trading_bot\*" ubuntu@<TU_IP_PUBLICA_ORACLE>:~/trading_bot/
```

#### Opción B: Crear los archivos directamente en la VPS:
Dentro del directorio `~/trading_bot/` en la VPS, crea el archivo `.env`:

```bash
nano .env
```
Pega tus credenciales de Telegram:
```env
TELEGRAM_BOT_TOKEN="Tu_Token_Aqui"
TELEGRAM_CHAT_ID="Tu_Chat_ID_Aqui"
INITIAL_CAPITAL="1000.0"
CHECK_INTERVAL_SECONDS="180"
```
Guarda con `Ctrl + O`, presiona `Enter` y sal con `Ctrl + X`.

---

### Paso 6: Configurar Ejecución Automática 24/7 con `systemd`

Para que el bot se ejecute continuamente de fondo y **se reinicie automáticamente** si la VPS se reinicia o falla la conexión:

1. Crea el archivo de servicio `systemd`:
```bash
sudo nano /etc/systemd/system/tradingbot.service
```

2. Pega la siguiente configuración (ajusta la ruta de usuario):
```ini
[Unit]
Description=Bot de Trading Cuantitativo Institucional 24/7
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/trading_bot
ExecStart=/home/ubuntu/trading_bot/venv/bin/python run_institutional_bot.py
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

3. Guarda con `Ctrl + O` $\rightarrow$ `Enter` $\rightarrow$ `Ctrl + X`.

4. Activa e inicia el servicio:
```bash
# Recargar demonio de servicios
sudo systemctl daemon-reload

# Habilitar servicio al encender el sistema
sudo systemctl enable tradingbot

# Iniciar el bot ahora mismo
sudo systemctl start tradingbot

# Ver el estado en vivo del bot
sudo systemctl status tradingbot
```

---

## 📊 3. Comandos de Monitoreo Útiles en la Nube

| Acción | Comando en la VPS |
| :--- | :--- |
| **Ver logs en tiempo real** | `sudo journalctl -u tradingbot -f` |
| **Ver estado del servicio** | `sudo systemctl status tradingbot` |
| **Reiniciar el bot** | `sudo systemctl restart tradingbot` |
| **Detener el bot** | `sudo systemctl stop tradingbot` |
| **Ver cartera demo en JSON** | `cat ~/trading_bot/paper_portfolio.json` |

---

> [!TIP]
> **Ventaja Institucional**: Al hospedar el bot en Oracle Cloud (región US-East), la latencia de conexión con los servidores de OKX Spot se reduce de ~120 ms (desde conexión de hogar) a **menos de 15 ms**, asegurando que tus órdenes Límite Maker entren en el primer milisegundo tras la confirmación de la vela 4H/1H.
