# Keep the service templates in a library chart

Some applications are several processes that only make sense together: a server and its controller, a coordinator and its workers. Described as separate services, they repeat their shared configuration in every file and are released as loosely related pieces. A product also sometimes needs a few resources of its own beside those processes, without giving up the service definition for the processes themselves.

Mitis therefore keeps every template of the service chart in a library chart, **service-library**. The service chart remains the same application chart, with the same values, schema and rendered output, and only calls the library. A product-owned chart can depend on the library and render any number of services in one release, each from a values map in the service vocabulary, next to templates of its own.

This makes two named templates a public contract: `mitis.service.render`, which renders one service from a values map, and `mitis.service.defaults`, the service chart's defaults that it applies first. The service chart's schema validates its own values only; a composing chart owns the schema of the fields it exposes, while the library's semantic validation still runs for every service it renders.

Three alternatives were rejected:

- **An umbrella chart over a vendor chart.** Helm cannot compute a subchart's values from its parent's values, so the interface of such a chart stays the vendor's, and the common fields (capacity, placement, exposure, inputs) cannot be shared with first-party services.
- **A composition controller that installs vendor Helm releases** from a typed resource. It gives a neutral interface, but adds a second deployment engine and hides the workload's rendered changes from the tool that reviews and applies everything else.
- **Depending on the service chart itself and switching its output off.** A dependency that exists only to lend its templates is what a library chart is for, and an application chart used that way would still be configured through a nested values key.

Vendor charts remain consumable directly with their own values. The library is for products that choose to run a process on the service definition instead.
