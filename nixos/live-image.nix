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
      exec chromium \
        --kiosk \
        --no-first-run \
        --noerrdialogs \
        --disable-gpu \
        --disable-dev-shm-usage \
        --disable-features=Translate,MediaRouter \
        --disable-session-crashed-bubble \
        --disable-infobars \
        --incognito \
        --user-data-dir=/tmp/driveproof-chromium \
        http://127.0.0.1:5055/
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
    after = [ "network.target" ];
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

  # The app stays root because SMART, raw device reads, and device power actions
  # require elevated permissions. The browser itself runs as the kiosk user.
  system.stateVersion = "24.11";
}
