## 🚀 Season 5: Expansion & Connectivity

### 🏗️ Infrastructure (Phase 5.0)
- **Raspberry Pi IP**: Fixed (Static IP) via NetworkManager.
- **External Access (Dashboard)**:
  - Tool: ngrok (Free Plan / Static Domain)
  - URL: Fixed Domain (See config)
  - Auth: Basic Auth (Haruna / Masahiro)
  - Startup: Systemd service (`ngrok.service`)
- **Remote Management (SSH)**:
  - Tool: Tailscale
  - Access: VPN Mesh Network (No port forwarding required)

### ⚠️ Security Notes
- `ngrok.yml` and `.env` are excluded from git.
- SSH access is restricted to Tailscale network.