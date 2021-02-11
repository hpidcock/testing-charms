#!/usr/bin/env python3
# Copyright 2020 Harry Pidcock
# See LICENSE file for licensing details.

import logging
import os
import urllib
import json

from ops.charm import CharmBase
from ops.main import main
from ops.framework import StoredState
from ops.model import (
    ActiveStatus,
    Relation,
    Binding,
    WaitingStatus,
)


logger = logging.getLogger(__name__)


class CockroachDBCharm(CharmBase):
    _stored = StoredState()

    def __init__(self, *args):
        super().__init__(*args)
        self.framework.observe(self.on.start, self._start)
        self.framework.observe(self.on.upgrade_charm, self._start)
        self.framework.observe(self.on.update_status, self._update_status)
        self.framework.observe(self.on.stop, self._stop)
        self.framework.observe(self.on.cockroachdb_workload_ready, self._cockroachdb_ready)

        self.framework.observe(self.on["db"].relation_joined, self._on_joined)
        self.framework.observe(self.on["db"].relation_changed, self._on_changed)
        self.framework.observe(self.on["db"].relation_broken, self._on_broken)

    def _start(self, event):
        logger.error("start hook")
        self.unit.status = WaitingStatus("cockroachdb pending")

    def _stop(self, event):
        logger.error("stop hook")
        self.unit.get_container("cockroachdb").stop("cockroachdb")
        self.unit.status = WaitingStatus("cockroachdb stopping")

    def _update_status(self, event):
        logger.error("update status hook")

    def _on_joined(self, event):
        logger.error("relation joined hook")
        rel: Relation = event.relation
        remote = rel.data[rel.app]
        if "database" not in remote:
            event.defer()
            return
        db = remote["database"]

        if not os.path.exists("/charm/containers/cockroachdb/pebble/layers"):
            os.makedirs("/charm/containers/cockroachdb/pebble/layers")
        with open("/charm/containers/cockroachdb/pebble/layers/1-{}.yaml".format(rel.app.name), "w") as f:
            f.write("""
summary: CockroachDB relation layer
description: CockroachDB relation layer
services:
    sql-create-db-{}:
        override: replace
        summary: sql create database
        after: 
            - cockroachdb
        command: cockroach sql --insecure --execute 'CREATE DATABASE IF NOT EXISTS {}'
""".format(db, db))
        try:
            self.unit.get_container("cockroachdb").start("sql-create-db-{}".format(db))
        except urllib.error.HTTPError as e:
            logger.error(e)
            obj = json.load(e)
            logger.error(json.dumps(obj, sort_keys=True, indent=4))
        self._on_changed(event)

    def _on_changed(self, event):
        logger.error("relation changed hook")
        rel: Relation = event.relation
        remote = rel.data[rel.app]
        db = remote["database"]
        local = rel.data[self.app]
        binding: Binding = self.model.get_binding(rel)

        local["master"] = "dbname={} user=root host={} port=26257 sslmode=disable".format(db, binding.network.bind_address)
        local["database"] = db
        if "extensions" in remote:
            local["extensions"] = remote["extensions"]
        if "roles" in remote:
            local["roles"] = remote["roles"]
        if "egress-subnets" in remote:
            local["allowed-subnets"] = remote["egress-subnets"]
        local["standbys"] = ""

    def _on_broken(self, event):
        logger.error("relation broken hook")

    def _cockroachdb_ready(self, event):
        logger.error("cockroachdb workload ready hook")
        if not os.path.exists("/charm/containers/cockroachdb/pebble/layers"):
            os.makedirs("/charm/containers/cockroachdb/pebble/layers")
        with open("/charm/containers/cockroachdb/pebble/layers/0.yaml", "w") as f:
            f.write("""
summary: CockroachDB layer
description: CockroachDB layer
services:
    cockroachdb:
        override: replace
        summary: cockroachdb service
        command: cockroach start-single-node --insecure --store=/cockroach/cockroach-data
""")
        self.unit.get_container("cockroachdb").start("cockroachdb")
        self.unit.status = ActiveStatus("cockroachdb started")


if __name__ == "__main__":
    main(CockroachDBCharm, use_juju_for_storage=True)
