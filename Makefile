.PHONY: seed

seed:
	docker compose exec api python manage.py seed
