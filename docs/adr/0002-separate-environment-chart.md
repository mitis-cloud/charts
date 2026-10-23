# Give each environment its own cluster-scoped chart

Routing, certificates, firewall policy and maintenance state belong to a deployment environment, not to any one service. They are identical for every service in the environment. Their Kubernetes objects are also cluster-scoped, while the service chart is namespace-scoped by design.

Mitis therefore adds a third chart, **environment**, installed as one release per environment. It renders one cluster-scoped neutral composite resource. That composite owns the environment's namespace, its exposure profiles and its maintenance switch.

Services keep declaring only `http.exposure: public|private`, which selects one of the environment's exposure profiles. This accepts a third chart, alongside service and delivery, so that no service instance owns environment-wide resources. It also keeps environments independent: adding one never edits a shared cluster-wide file.
