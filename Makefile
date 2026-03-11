UV ?= uv
NPM ?= npm
BACKEND_DIR := backend
DESKTOP_DIR := desktop

.PHONY: setup backend-dev backend-test backend-build desktop-dev desktop-test desktop-build lint test build install-systemd-user

setup:
	$(UV) sync --project $(BACKEND_DIR) --extra dev
	cd $(DESKTOP_DIR) && $(NPM) install

backend-dev:
	$(UV) run --project $(BACKEND_DIR) --directory $(BACKEND_DIR) python -m infostream.cli

backend-test:
	$(UV) run --project $(BACKEND_DIR) --directory $(BACKEND_DIR) pytest -q

backend-build:
	mkdir -p $(DESKTOP_DIR)/src-tauri/bin .build/pyinstaller
	$(UV) run --project $(BACKEND_DIR) --directory $(BACKEND_DIR) \
		pyinstaller --onefile --name infostream-backend --paths src src/infostream/cli.py \
		--distpath ../$(DESKTOP_DIR)/src-tauri/bin --workpath ../.build/pyinstaller/work --specpath ../.build/pyinstaller/spec
	TARGET_TRIPLE="$$(rustc -vV | sed -n 's/^host: //p')"; \
	if [ -n "$$TARGET_TRIPLE" ]; then \
		cp $(DESKTOP_DIR)/src-tauri/bin/infostream-backend $(DESKTOP_DIR)/src-tauri/bin/infostream-backend-$$TARGET_TRIPLE; \
	fi


desktop-dev:
	cd $(DESKTOP_DIR) && env -u LD_LIBRARY_PATH -u SNAP -u SNAP_NAME -u SNAP_REVISION -u SNAP_ARCH $(NPM) run tauri dev

desktop-test:
	cd $(DESKTOP_DIR) && $(NPM) run test

desktop-build: backend-build
	cd $(DESKTOP_DIR) && $(NPM) run tauri build

lint:
	$(UV) run --project $(BACKEND_DIR) --directory $(BACKEND_DIR) ruff check src tests
	$(UV) run --project $(BACKEND_DIR) --directory $(BACKEND_DIR) mypy src

test: backend-test desktop-test

build: backend-build desktop-build

install-systemd-user:
	mkdir -p $$HOME/.config/systemd/user
	cp services/linux/infostream.service $$HOME/.config/systemd/user/infostream.service
	systemctl --user daemon-reload
	systemctl --user enable --now infostream.service
