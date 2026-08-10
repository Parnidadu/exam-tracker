.PHONY: seed deploy deploy-staging

seed:
	docker compose exec api python manage.py seed

# One command per environment. ENV picks the .env.<ENV> file; everything
# else - build order, migrations, static files, the deployment check - is
# the same for both, because staging that deploys differently is not a
# rehearsal of production.
deploy:
	./deploy/deploy.sh $(or $(ENV),prod)

deploy-staging:
	./deploy/deploy.sh staging
