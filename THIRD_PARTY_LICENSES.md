# Third-Party Licenses

This project depends on or distributes third-party software with its own licenses.

This file is a practical overview, not a substitute for each upstream license text.
When distributing a built ISO image, preserve upstream notices and provide access
to the corresponding source code where required by the applicable licenses.

## Core application dependencies

### Flask
- Purpose: web framework
- License: BSD-3-Clause
- Source: https://flask.palletsprojects.com/en/stable/license/

### Python
- Purpose: runtime
- License: Python Software Foundation License Version 2
- Source: https://docs.python.org/3/license.html

### Chart.js
- Purpose: SMART chart rendering in the browser
- License: MIT
- Source: https://github.com/chartjs/Chart.js/blob/master/LICENSE.md

## System tools used by DriveProof

### smartmontools / smartctl
- Purpose: SMART data, self-tests
- License: GNU GPL
- Source: https://www.smartmontools.org/

### hdparm
- Purpose: ATA identify and secure erase support
- License: BSD-style license as published by upstream project
- Source: https://sourceforge.net/projects/hdparm/

### util-linux
- Purpose: block device utilities such as `lsblk`
- License: mixed upstream licensing; default license in util-linux is GPL-2.0-or-later unless otherwise specified per file
- Source: https://github.com/util-linux/util-linux/blob/master/README.licensing

### UDisks / udisksctl
- Purpose: safe unmount and power-off
- License: daemon and tools GPLv2-or-later; libraries LGPLv2-or-later
- Source: https://github.com/storaged-project/udisks

### Chromium
- Purpose: kiosk browser in the NixOS live image
- License: Chromium is open source and includes multiple components; the official GitHub mirror identifies BSD-3-Clause licenses in the top-level mirror
- Source: https://github.com/chromium/chromium
- Project site: https://www.chromium.org/

## Nix / NixOS build layer

### Nixpkgs
- Purpose: build expressions, NixOS modules, package set
- License: MIT for the Nix expressions and modules in nixpkgs itself
- Note: this does not change the licenses of the packages built through nixpkgs
- Source: https://github.com/NixOS/nixpkgs

## Distribution notes

If you distribute the source repository only, the main license for the DriveProof
project itself is MIT. Third-party tools remain under their own licenses.

If you distribute a prebuilt ISO image or a preinstalled USB stick, you are
distributing binaries of multiple upstream projects. In that case you should:

1. keep this third-party license overview with the image,
2. keep the DriveProof `LICENSE` file with the image,
3. preserve upstream attribution and notices,
4. provide the corresponding source code or a valid source offer where the
   applicable license requires it.

## No legal advice

This file is technical project documentation, not legal advice. For commercial
distribution at scale, get legal review for your exact distribution model.
