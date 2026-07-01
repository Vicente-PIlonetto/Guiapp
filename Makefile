.PHONY: install backend frontend modules dev clean

install:
	python -m pip install -r requirements.txt
	cd frontend && npm install

backend:
	python -m uvicorn backend.main:app --host $${APP_HOST:-0.0.0.0} --port $${APP_PORT:-8000}

frontend:
	cd frontend && npm run dev -- --host 0.0.0.0

modules:
	$(MAKE) -C modules

dev:
	python tui.py

clean:
	$(MAKE) -C modules clean
