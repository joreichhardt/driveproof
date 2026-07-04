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
    ps.flask
  ]);

  kioskSession = pkgs.writeShellApplication {
    name = "driveproof-kiosk-session";
    runtimeInputs = [
      pkgs.coreutils
      pkgs.curl
      pkgs.chromium
      pkgs.openbox
      pkgs.xorg.xset
    ];
    text = ''
      xset -dpms
      xset s off
      xset s noblank
      openbox &
      for _ in $(seq 1 120); do
        if curl --silent --fail http://127.0.0.1:5055/ >/dev/null; then
          break
        fi
        sleep 1
      done
      exec chromium \
        --kiosk \
        --no-first-run \
        --disable-features=Translate,MediaRouter \
        --disable-session-crashed-bubble \
        --disable-infobars \
        --incognito \
        http://127.0.0.1:5055/
    '';
  };

  xinitrc = pkgs.writeText "xinitrc" ''
    #!${pkgs.runtimeShell}
    exec ${kioskSession}/bin/driveproof-kiosk-session
  '';

in {
  imports = [
    (modulesPath + "/installer/cd-dvd/installation-cd-graphical-base.nix")
  ];

  isoImage.isoName = lib.mkForce "driveproof-live.iso";

  networking.hostName = "driveproof-live";
  time.timeZone = "Europe/Berlin";

  services.xserver.enable = true;

  users.users.kiosk = {
    isNormalUser = true;
    description = "Kiosk User";
    extraGroups = [ "video" "audio" "input" "disk" ];
    shell = pkgs.bashInteractive;
    initialPassword = "kiosk";
  };

  environment.systemPackages = with pkgs; [
    bashInteractive
    chromium
    curl
    eject
    hdparm
    openbox
    smartmontools
    udisks
    util-linux
    xorg.xauth
    xorg.xinit
    xorg.xorgserver
  ];

  systemd.services.driveproof = {
    description = "DriveProof";
    wantedBy = [ "multi-user.target" ];
    after = [ "network.target" ];
    serviceConfig = {
      Type = "simple";
      Restart = "always";
      RestartSec = 2;
      WorkingDirectory = appSrc;
      ExecStart = "${pythonEnv}/bin/python ${appSrc}/app.py";
    };
  };

  systemd.services.kiosk-session = {
    description = "Kiosk Chromium Session";
    wantedBy = [ "multi-user.target" ];
    after = [ "systemd-user-sessions.service" "network-online.target" "driveproof.service" ];
    wants = [ "network-online.target" ];
    conflicts = [ "getty@tty1.service" ];
    serviceConfig = {
      User = "kiosk";
      Group = "users";
      WorkingDirectory = "/home/kiosk";
      PAMName = "login";
      TTYPath = "/dev/tty1";
      TTYReset = true;
      TTYVHangup = true;
      TTYVTDisallocate = true;
      StandardInput = "tty";
      StandardOutput = "journal";
      StandardError = "journal";
      UtmpIdentifier = "tty1";
      UtmpMode = "user";
      Restart = "always";
      RestartSec = 2;
      ExecStart = "${pkgs.xorg.xinit}/bin/startx ${xinitrc} -- :0 vt1 -keeptty";
    };
  };

  # The app stays root because SMART, raw device reads, and device power actions
  # require elevated permissions. The browser itself runs as the kiosk user.
  system.stateVersion = "24.11";
}
