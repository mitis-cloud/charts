.PHONY: deps test package check

deps:
	helm dependency build charts/service
	helm dependency build tests/fixtures/composed

test: deps
	python3 -m unittest discover -s tests -v

check: test
	python3 tests/validate_resources.py

package: deps
	mkdir -p dist
	helm package charts/service-library --destination dist
	helm package charts/service --destination dist
	helm package charts/delivery --destination dist
	helm package charts/environment --destination dist
	crossplane xpkg build --package-root crossplane --examples-root examples/crossplane -o dist/platform.xpkg
