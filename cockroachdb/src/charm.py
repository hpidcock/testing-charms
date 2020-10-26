#!/usr/bin/env python3
# Copyright 2020 Harry Pidcock
# See LICENSE file for licensing details.

import logging
import os
import subprocess
import json

from ops.charm import CharmBase
from ops.main import main
from ops.framework import StoredState
from ops.model import (
    ActiveStatus,
    UnknownStatus,
    Relation,
    Binding,
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

        self.framework.observe(self.on["db"].relation_joined, self._on_joined)
        self.framework.observe(self.on["db"].relation_changed, self._on_changed)
        self.framework.observe(self.on["db"].relation_broken, self._on_broken)

    def _start(self, event):
        if self._is_running():
            return
        os.system("pebble start --socket /charm/containers/cockroachdb/pebble.sock -- cockroach start-single-node --insecure --store=/cockroach/cockroach-data")
        self.unit.status = ActiveStatus("cockroachdb started")

    def _stop(self, event):
        os.system("pebble signal --socket /charm/containers/cockroachdb/pebble.sock")

    def _update_status(self, event):
        code = os.system("pebble exec --socket /charm/containers/cockroachdb/pebble.sock -- cockroach sql --insecure --execute 'SELECT 1'")
        if code == 0:
            self.unit.status = ActiveStatus("cockroachdb alive")
        else:
            self.unit.status = UnknownStatus("cockroachdb dead")

    def _on_joined(self, event):
        self._start(event) 

        rel: Relation = event.relation
        remote = rel.data[rel.app]
        db = remote["database"]
        os.system("pebble exec --socket /charm/containers/cockroachdb/pebble.sock -- cockroach sql --insecure --execute 'CREATE DATABASE {}'".format(db))

        self._on_changed(event)

    def _on_changed(self, event):
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
        logger.error("got relation_broken")

    def _is_running(self):
        res = subprocess.run(["pebble", "status", "--socket", "/charm/containers/cockroachdb/pebble.sock"], capture_output=True, text=True)
        logger.error(res.returncode)
        logger.error(res.stdout)
        s = json.loads(res.stdout)
        return s["status"] == "running"



if __name__ == "__main__":
    main(CockroachDBCharm, use_juju_for_storage=True)
