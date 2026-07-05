{ pkgs, lib, modulesPath, ... }:
let
  appSrc = lib.cleanSourceWith {
    src = ../.;
    filter = path: _type:
      let
        base = builtins.baseNameOf path;
      in
      ! builtins.elem base [ ".venv" "__pycache__" "result" "reports" ".git" "state.db" ];
  };

  pythonEnv = pkgs.python3.withPackages (ps: [
    ps.cryptography
    ps.flask
  ]);

  kioskSession = pkgs.writeShellApplication {
    name = "driveproof-kiosk-session";
    runtimeInputs = [
      pkgs.coreutils
      pkgs.curl
      pkgs.feh
      pkgs.chromium
      pkgs.openbox
      pkgs.xterm
      pkgs.xorg.xsetroot
      pkgs.xorg.xset
    ];
    text = ''
      xset -dpms
      xset s off
      xset s noblank
      xsetroot -solid "#f4f1ea"
      openbox &
      sleep 1
      feh --no-fehbg --bg-center ${../static/assets/driveproof-logo.png} || true
      status_file=/tmp/driveproof-kiosk-status.txt
      echo "DriveProof is starting..." > "$status_file"
      for _ in $(seq 1 120); do
        if curl --silent --fail http://127.0.0.1:5055/ >/dev/null; then
          break
        fi
        sleep 1
      done
      if ! curl --silent --fail http://127.0.0.1:5055/ >/dev/null; then
        echo "DriveProof did not start. Check: systemctl status driveproof" > "$status_file"
        exec xterm -geometry 140x42 -fa Monospace -fs 18 -e "cat '$status_file'; echo; journalctl -u driveproof -n 80 --no-pager; echo; read -p 'Press Enter to open a shell...'; exec bash"
      fi
      profile_dir=/tmp/driveproof-chromium
      download_dir=/run/media/driveproof/DRVTOOLS/DriveProof-Vendor-Tools/Downloads
      mkdir -p "$profile_dir/Default"
      cat > "$profile_dir/Default/Preferences" <<EOF
      {
        "download": {
          "default_directory": "$download_dir",
          "directory_upgrade": true,
          "prompt_for_download": false
        },
        "profile": {
          "default_content_setting_values": {
            "automatic_downloads": 1
          }
        },
        "safebrowsing": {
          "enabled": true
        }
      }
EOF
      exec chromium \
        --kiosk \
        --no-first-run \
        --noerrdialogs \
        --disable-gpu \
        --disable-dev-shm-usage \
        --disable-features=Translate,MediaRouter \
        --disable-session-crashed-bubble \
        --disable-infobars \
        --disable-popup-blocking \
        --download-default-directory="$download_dir" \
        --user-data-dir="$profile_dir" \
        http://127.0.0.1:5055/
    '';
  };

  driveproofMountExports = pkgs.writeShellApplication {
    name = "driveproof-mount-exports";
    runtimeInputs = [
      pkgs.coreutils
      pkgs.e2fsprogs
      pkgs.util-linux
    ];
    text = ''
      set -euo pipefail

      mount_root=/run/media/driveproof
      mkdir -p "$mount_root"

      mount_label() {
        label="$1"
        options="$2"
        device="$(blkid -L "$label" 2>/dev/null || true)"
        mount_point="$mount_root/$label"
        mkdir -p "$mount_point"
        if [ -n "$device" ] && ! mountpoint -q "$mount_point"; then
          mount -o "$options" "$device" "$mount_point" || true
        fi
      }

      mount_label DRVPROOF rw,umask=000
      mount_label DRVTOOLS rw

      tools_dir="$mount_root/DRVTOOLS/DriveProof-Vendor-Tools"
      downloads_dir="$tools_dir/Downloads"
      if mountpoint -q "$mount_root/DRVTOOLS"; then
        mkdir -p "$downloads_dir"
        chmod 0777 "$tools_dir" "$downloads_dir" || true
      fi
    '';
  };

  driveproofNetworkConfig = pkgs.writeShellApplication {
    name = "driveproof-apply-network-config";
    runtimeInputs = [
      pkgs.coreutils
      pkgs.gawk
      pkgs.gnugrep
      pkgs.iproute2
      pkgs.networkmanager
      pkgs.util-linux
    ];
    text = ''
      set -euo pipefail

      mount_root=/run/media/driveproof
      mount_point="$mount_root/DRVPROOF"
      config_file="$mount_point/driveproof-network.conf"

      mkdir -p "$mount_point"
      device="$(blkid -L DRVPROOF 2>/dev/null || true)"
      if [ -n "$device" ] && ! mountpoint -q "$mount_point"; then
        mount -o rw,umask=000 "$device" "$mount_point" || true
      fi

      if [ ! -f "$config_file" ]; then
        exit 0
      fi

      get_value() {
        awk -F= -v key="$1" '
          $1 == key {
            sub(/^[ \t]+/, "", $2);
            sub(/[ \t]+$/, "", $2);
            print $2;
            exit;
          }
        ' "$config_file"
      }

      ip_addr="$(get_value ip || true)"
      gateway="$(get_value gw || true)"
      dns="$(get_value dns || true)"

      if [ -z "$ip_addr" ] || [ -z "$gateway" ]; then
        exit 0
      fi

      iface="$(nmcli -t -f DEVICE,TYPE,STATE device status | awk -F: '$2 == "ethernet" { print $1; exit }')"
      if [ -z "$iface" ]; then
        exit 0
      fi

      nmcli connection delete driveproof-static >/dev/null 2>&1 || true
      nmcli connection add type ethernet ifname "$iface" con-name driveproof-static autoconnect yes \
        ipv4.method manual ipv4.addresses "$ip_addr" ipv4.gateway "$gateway" \
        ipv6.method ignore

      if [ -n "$dns" ]; then
        nmcli connection modify driveproof-static ipv4.dns "$dns"
      fi

      nmcli connection up driveproof-static || true
    '';
  };

in {
  imports = [
    (modulesPath + "/installer/cd-dvd/installation-cd-graphical-base.nix")
  ];

  isoImage.isoName = lib.mkForce "driveproof-live.iso";
  isoImage.appendToMenuLabel = lib.mkForce " Live System";
  isoImage.splashImage = ../static/assets/driveproof-logo.png;
  isoImage.efiSplashImage = ../static/assets/driveproof-logo.png;
  isoImage.squashfsCompression = lib.mkForce "gzip -no-compression";

  networking.hostName = "driveproof-live";
  networking.networkmanager.enable = true;
  time.timeZone = "Europe/Berlin";
  system.nixos.distroName = "DriveProof";
  boot.kernelParams = [
    "console=tty0"
    "console=ttyS0,115200n8"
  ];

  services.xserver.enable = true;
  services.displayManager.enable = true;
  services.displayManager.autoLogin = {
    enable = true;
    user = "kiosk";
  };
  services.displayManager.defaultSession = "driveproof";
  services.xserver.displayManager.lightdm.enable = true;
  services.xserver.displayManager.session = [
    {
      manage = "window";
      name = "none";
      start = "";
    }
    {
      manage = "desktop";
      name = "driveproof";
      start = ''
        exec ${kioskSession}/bin/driveproof-kiosk-session
      '';
    }
  ];

  users.users.kiosk = {
    isNormalUser = true;
    description = "Kiosk User";
    extraGroups = [ "video" "audio" "input" "disk" "networkmanager" ];
    shell = pkgs.bashInteractive;
    initialPassword = "kiosk";
  };

  environment.systemPackages = with pkgs; [
    bashInteractive
    chromium
    curl
    dpkg
    dosfstools
    eject
    exfatprogs
    hdparm
    lsscsi
    nvme-cli
    openbox
    parted
    rpmextract
    sg3_utils
    smartmontools
    unzip
    udisks
    util-linux
    xorg.xauth
    xorg.xorgserver
  ];

  environment.etc."driveproof/LICENSE".source = ../LICENSE;
  environment.etc."driveproof/THIRD_PARTY_LICENSES.md".source = ../THIRD_PARTY_LICENSES.md;
  environment.etc."driveproof/COMMERCIAL_SERVICES.md".source = ../COMMERCIAL_SERVICES.md;

  systemd.services.driveproof = {
    description = "DriveProof";
    wantedBy = [ "multi-user.target" ];
    after = [ "network.target" "driveproof-mount-exports.service" ];
    requires = [ "driveproof-mount-exports.service" ];
    path = with pkgs; [
      coreutils
      dpkg
      dosfstools
      eject
      exfatprogs
      hdparm
      lsscsi
      nvme-cli
      parted
      rpmextract
      sg3_utils
      smartmontools
      unzip
      udisks
      util-linux
    ];
    serviceConfig = {
      Type = "simple";
      Restart = "always";
      RestartSec = 2;
      StateDirectory = "driveproof";
      WorkingDirectory = appSrc;
      Environment = "DRIVEPROOF_STATE_DIR=/var/lib/driveproof";
      ExecStart = "${pythonEnv}/bin/python ${appSrc}/app.py";
    };
  };

  systemd.services.driveproof-network-config = {
    description = "Apply DriveProof static network config from DRVPROOF";
    wantedBy = [ "multi-user.target" ];
    after = [ "NetworkManager.service" "driveproof-mount-exports.service" ];
    requires = [ "driveproof-mount-exports.service" ];
    before = [ "driveproof.service" ];
    path = with pkgs; [
      coreutils
      gawk
      gnugrep
      iproute2
      networkmanager
      util-linux
    ];
    serviceConfig = {
      Type = "oneshot";
      ExecStart = "${driveproofNetworkConfig}/bin/driveproof-apply-network-config";
    };
  };

  systemd.services.driveproof-mount-exports = {
    description = "Mount DriveProof USB export and tools partitions";
    wantedBy = [ "multi-user.target" ];
    before = [ "driveproof.service" "display-manager.service" ];
    path = with pkgs; [
      coreutils
      e2fsprogs
      util-linux
    ];
    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
      ExecStart = "${driveproofMountExports}/bin/driveproof-mount-exports";
    };
  };

  # The app stays root because SMART, raw device reads, and device power actions
  # require elevated permissions. The browser itself runs as the kiosk user.
  system.stateVersion = "24.11";
}
