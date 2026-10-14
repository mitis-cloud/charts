.PHONY: test package check

test:
	python3 -m unittest discover -s tests -v

check: test
	python3 tests/validate_resources.py

package:
	mkdir -p dist
	helm package charts/service --destination dist
	helm package charts/delivery --destination dist
