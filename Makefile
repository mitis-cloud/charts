.PHONY: test package check

test:
	python3 -m unittest discover -s tests -v

check: test
	python3 tests/validate_resources.py

package:
	mkdir -p dist
	helm package charts/service --destination dist
	helm package charts/delivery --destination dist
	helm package charts/environment --destination dist
	helm package charts/webapp --destination dist
	crossplane xpkg build --package-root crossplane --examples-root examples/crossplane -o dist/platform.xpkg
