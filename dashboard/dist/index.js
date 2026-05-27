/**
 * provider-auto-switch — Dashboard Plugin
 *
 * Automatic model switching UI: profile management, strategy config,
 * manual override, scan results, switch history.
 *
 * Plain IIFE, no build step. Uses window.__HERMES_PLUGIN_SDK__ for React +
 * shadcn primitives.
 */
(function () {
  "use strict";

  const SDK = window.__HERMES_PLUGIN_SDK__;
  if (!SDK) return;

  const { React } = SDK;
  const h = React.createElement;
  const {
    Card, CardContent, CardHeader, CardTitle,
    Badge, Button, Input, Label, Select, SelectOption,
  } = SDK.components;
  const { useState, useEffect, useCallback, useMemo } = SDK.hooks;
  const { cn } = SDK.utils;

  // -----------------------------------------------------------------------
  // Colors
  // -----------------------------------------------------------------------
  const STATUS_COLORS = {
    active: "#9ece6a",
    limited: "#e0af68",
    unavailable: "#f7768e",
    unknown: "#565f89",
  };

  // -----------------------------------------------------------------------
  // API helpers
  // -----------------------------------------------------------------------
  const BASE = "/api/plugins/provider-auto-switch";

  async function api(path, opts) {
    const res = await fetch(BASE + path, {
      headers: { "Content-Type": "application/json" },
      ...opts,
    });
    return res.json();
  }

  // -----------------------------------------------------------------------
  // StatusBadge
  // -----------------------------------------------------------------------
  function StatusBadge({ status, label }) {
    const color = STATUS_COLORS[status] || STATUS_COLORS.unknown;
    return h("span", {
      style: {
        display: "inline-flex",
        alignItems: "center",
        gap: "4px",
        fontSize: "13px",
        color: color,
        fontWeight: 600,
      },
    }, [
      h("span", {
        style: {
          width: "8px", height: "8px", borderRadius: "50%",
          backgroundColor: color, display: "inline-block",
        },
      }),
      label || status,
    ]);
  }

  // -----------------------------------------------------------------------
  // StatsCard
  // -----------------------------------------------------------------------
  function StatsCard({ stats }) {
    if (!stats) return null;
    const items = [
      { label: "Profiles", value: stats.total_profiles, color: "#7aa2f7" },
      { label: "Auto", value: stats.auto_switch_enabled, color: "#9ece6a" },
      { label: "Manual", value: stats.manual_override_active, color: "#e0af68" },
      { label: "Unhealthy", value: stats.unhealthy, color: "#f7768e" },
    ];
    return h(Card, { style: { marginBottom: "16px" } },
      h(CardContent, { style: { display: "flex", gap: "24px", padding: "16px 20px" } },
        items.map(function (item) {
          return h("div", { key: item.label, style: { textAlign: "center", minWidth: "80px" } }, [
            h("div", { style: { fontSize: "28px", fontWeight: 700, color: item.color } }, String(item.value)),
            h("div", { style: { fontSize: "12px", color: "#565f89", marginTop: "2px" } }, item.label),
          ]);
        })
      )
    );
  }

  // -----------------------------------------------------------------------
  // ProfileRow
  // -----------------------------------------------------------------------
  function ProfileRow({ profile, isSelected, onClick }) {
    const combo = profile.active_combo;
    const cfg = profile.config;
    const status = combo ? "active" : "unavailable";
    return h("div", {
      onClick: onClick,
      style: {
        display: "flex", alignItems: "center", gap: "12px",
        padding: "10px 16px", cursor: "pointer",
        borderRadius: "8px",
        backgroundColor: isSelected ? "rgba(122,162,247,0.08)" : "transparent",
        borderLeft: isSelected ? "3px solid #7aa2f7" : "3px solid transparent",
        transition: "background-color 0.15s",
      },
      onMouseEnter: function (e) { e.currentTarget.style.backgroundColor = "rgba(122,162,247,0.04)"; },
      onMouseLeave: function (e) {
        if (!isSelected) e.currentTarget.style.backgroundColor = "transparent";
      },
    }, [
      h("div", { style: { flex: "0 0 120px", fontWeight: 600, color: "#c0caf5" } }, profile.profile_name),
      h("div", { style: { flex: 1, display: "flex", alignItems: "center", gap: "8px" } }, [
        combo
          ? [h("span", { style: { fontSize: "13px", color: "#a9b1d6" } }, combo.model_name),
             h("span", { style: { fontSize: "12px", color: "#565f89" } }, combo.provider_name)]
          : h("span", { style: { fontSize: "12px", color: "#565f89" } }, "Not configured"),
      ]),
      h(StatusBadge, { status: status, label: combo ? "Active" : "Inactive" }),
      h("span", { style: { fontSize: "16px", color: "#565f89" } }, isSelected ? "\u25B2" : "\u25BC"),
    ]);
  }

  // -----------------------------------------------------------------------
  // ConfigPanel
  // -----------------------------------------------------------------------
  function ConfigPanel({ profile, config, onUpdate, onScan, snapshot, loading }) {
    if (!config) return h("div", { style: { padding: "24px", textAlign: "center", color: "#565f89" } }, "No config yet — config will be auto-created on first use.");

    const [strategy, setStrategy] = React.useState(config.strategy);
    const [autoSwitch, setAutoSwitch] = React.useState(config.auto_switch);
    const [modelPrio, setModelPrio] = React.useState(config.model_priority || []);
    const [providerPrio, setProviderPrio] = React.useState(config.provider_priority || []);

    useEffect(function () {
      setStrategy(config.strategy);
      setAutoSwitch(config.auto_switch);
      setModelPrio(config.model_priority || []);
      setProviderPrio(config.provider_priority || []);
    }, [config]);

    function save() {
      onUpdate({
        strategy: strategy,
        auto_switch: autoSwitch,
        model_priority: modelPrio,
        provider_priority: providerPrio,
      });
    }

    function moveItem(arr, idx, dir) {
      var newArr = arr.slice();
      var target = idx + dir;
      if (target < 0 || target >= newArr.length) return arr;
      var tmp = newArr[target];
      newArr[target] = newArr[idx];
      newArr[idx] = tmp;
      return newArr;
    }

    function renderPriorityList(items, setItems) {
      return h("div", { style: { display: "flex", flexDirection: "column", gap: "4px" } },
        items.map(function (item, idx) {
          return h("div", {
            key: item,
            style: {
              display: "flex", alignItems: "center", gap: "6px",
              padding: "4px 8px", borderRadius: "4px",
              backgroundColor: "rgba(86,95,137,0.15)",
              fontSize: "13px",
            },
          }, [
            h("span", { style: { flex: 1, color: "#c0caf5" } }, item),
            h("button", {
              onClick: function () { setItems(moveItem(items, idx, -1)); },
              disabled: idx === 0,
              style: { cursor: idx === 0 ? "not-allowed" : "pointer", opacity: idx === 0 ? 0.3 : 1 },
            }, "\u25B2"),
            h("button", {
              onClick: function () { setItems(moveItem(items, idx, 1)); },
              disabled: idx === items.length - 1,
              style: { cursor: idx === items.length - 1 ? "not-allowed" : "pointer", opacity: idx === items.length - 1 ? 0.3 : 1 },
            }, "\u25BC"),
            h("button", {
              onClick: function () { setItems(items.filter(function (_, i) { return i !== idx; })); },
              style: { color: "#f7768e", cursor: "pointer" },
            }, "\u2715"),
          ]);
        })
      );
    }

    return h("div", { style: { display: "flex", flexDirection: "column", gap: "16px", padding: "16px 20px" } }, [
      // Strategy + Toggles
      h("div", { style: { display: "flex", gap: "16px", alignItems: "center", flexWrap: "wrap" } }, [
        h("div", { style: { display: "flex", alignItems: "center", gap: "6px" } }, [
          h(Label, { style: { fontSize: "12px", color: "#565f89" } }, "Strategy:"),
          h(Select, {
            value: strategy,
            onChange: function (e) { setStrategy(e.target.value); },
          }, [
            h(SelectOption, { value: "model_first" }, "Model First"),
            h(SelectOption, { value: "provider_first" }, "Provider First"),
          ]),
        ]),
        h("label", { style: { display: "flex", alignItems: "center", gap: "6px", fontSize: "13px", cursor: "pointer" } }, [
          h("input", {
            type: "checkbox", checked: autoSwitch,
            onChange: function (e) { setAutoSwitch(e.target.checked); },
          }),
          "Auto-switch",
        ]),
        h(Button, {
          variant: "outline", size: "sm",
          onClick: function () { onScan(); },
          disabled: loading,
        }, loading ? "Scanning..." : "Scan Now"),
        h(Button, {
          variant: "primary", size: "sm",
          onClick: save,
        }, "Save Config"),
      ]),

      // Priority Lists
      h("div", { style: { display: "grid", gridTemplateColumns: "1fr 1fr", gap: "24px" } }, [
        h("div", null, [
          h("div", { style: { fontSize: "12px", fontWeight: 600, color: "#565f89", marginBottom: "6px" } }, "Model Priority"),
          renderPriorityList(modelPrio, setModelPrio),
        ]),
        h("div", null, [
          h("div", { style: { fontSize: "12px", fontWeight: 600, color: "#565f89", marginBottom: "6px" } }, "Provider Priority"),
          renderPriorityList(providerPrio, setProviderPrio),
        ]),
      ]),

      // Snapshot Grid
      snapshot && h("div", null, [
        h("div", { style: { fontSize: "12px", fontWeight: 600, color: "#565f89", marginBottom: "8px" } }, "Scan Results"),
        h("div", { style: { overflowX: "auto" } },
          h("table", { style: { width: "100%", borderCollapse: "collapse", fontSize: "13px" } }, [
            h("thead", null,
              h("tr", { style: { borderBottom: "1px solid rgba(86,95,137,0.3)" } }, [
                h("th", { style: { textAlign: "left", padding: "6px 8px", color: "#565f89", fontWeight: 600 } }, "Model"),
                h("th", { style: { textAlign: "left", padding: "6px 8px", color: "#565f89", fontWeight: 600 } }, "Provider"),
                h("th", { style: { textAlign: "left", padding: "6px 8px", color: "#565f89", fontWeight: 600 } }, "Status"),
              ])
            ),
            h("tbody", null,
              Object.keys(snapshot).sort().map(function (model) {
                return snapshot[model].map(function (entry, ei) {
                  return h("tr", {
                    key: model + "-" + ei,
                    style: { borderBottom: "1px solid rgba(86,95,137,0.1)" },
                  }, [
                    h("td", { style: { padding: "5px 8px", color: "#c0caf5" } }, ei === 0 ? model : ""),
                    h("td", { style: { padding: "5px 8px", color: "#a9b1d6" } }, entry.provider),
                    h("td", { style: { padding: "5px 8px" } },
                      h(StatusBadge, {
                        status: entry.status,
                        label: entry.error_reason || entry.status,
                      })
                    ),
                  ]);
                });
              })
            ),
          ])
        ),
      ]),
    ]);
  }

  // -----------------------------------------------------------------------
  // HistoryPanel
  // -----------------------------------------------------------------------
  function HistoryPanel({ history }) {
    if (!history || history.length === 0) {
      return h("div", { style: { padding: "12px 20px", fontSize: "13px", color: "#565f89" } }, "No switch history yet.");
    }
    return h("div", { style: { overflowX: "auto" } },
      h("table", { style: { width: "100%", borderCollapse: "collapse", fontSize: "13px" } }, [
        h("thead", null,
          h("tr", { style: { borderBottom: "1px solid rgba(86,95,137,0.3)" } }, [
            h("th", { style: { textAlign: "left", padding: "6px 12px", color: "#565f89" } }, "Time"),
            h("th", { style: { textAlign: "left", padding: "6px 12px", color: "#565f89" } }, "From"),
            h("th", { style: { textAlign: "left", padding: "6px 12px", color: "#565f89" } }, "To"),
            h("th", { style: { textAlign: "left", padding: "6px 12px", color: "#565f89" } }, "Reason"),
            h("th", { style: { textAlign: "left", padding: "6px 12px", color: "#565f89" } }, "Triggered By"),
          ])
        ),
        h("tbody", null,
          history.map(function (hitem, idx) {
            return h("tr", {
              key: idx,
              style: { borderBottom: "1px solid rgba(86,95,137,0.1)" },
            }, [
              h("td", { style: { padding: "5px 12px", color: "#565f89", whiteSpace: "nowrap" } },
                (hitem.created_at || "").replace("T", " ").slice(0, 16)),
              h("td", { style: { padding: "5px 12px", color: "#a9b1d6" } },
                hitem.from_provider + "/" + hitem.from_model),
              h("td", { style: { padding: "5px 12px", color: "#9ece6a" } },
                hitem.to_provider + "/" + hitem.to_model),
              h("td", { style: { padding: "5px 12px", color: "#e0af68" } }, hitem.reason),
              h("td", { style: { padding: "5px 12px", color: "#565f89" } }, hitem.triggered_by),
            ]);
          })
        ),
      ])
    );
  }

  // -----------------------------------------------------------------------
  // Main App
  // -----------------------------------------------------------------------
  function App() {
    var _this = this;
    var _ = React.useState(null); var profiles = _[0]; var setProfiles = _[1];
    var _2 = React.useState(null); var stats = _2[0]; var setStats = _2[1];
    var _3 = React.useState(null); var selected = _3[0]; var setSelected = _3[1];
    var _4 = React.useState(null); var config = _4[0]; var setConfig = _4[1];
    var _5 = React.useState(null); var snapshot = _5[0]; var setSnapshot = _5[1];
    var _6 = React.useState(null); var history = _6[0]; var setHistory = _6[1];
    var _7 = React.useState(false); var loading = _7[0]; var setLoading = _7[1];

    function loadProfiles() {
      setLoading(true);
      Promise.all([
        api("/profiles"),
        api("/stats"),
      ]).then(function (results) {
        setProfiles(results[0].profiles);
        setStats(results[1]);
      }).catch(function (err) {
        console.error("Failed to load profiles:", err);
      }).finally(function () {
        setLoading(false);
      });
    }

    function loadProfileData(name) {
      setLoading(true);
      Promise.all([
        api("/" + name + "/config"),
        api("/" + name + "/snapshot"),
        api("/" + name + "/history"),
      ]).then(function (results) {
        setConfig(results[0].config);
        setSnapshot(results[1].snapshots);
        setHistory(results[2].history);
      }).catch(function (err) {
        console.error("Failed to load profile data:", err);
      }).finally(function () {
        setLoading(false);
      });
    }

    function selectProfile(name) {
      setSelected(name);
      loadProfileData(name);
    }

    function updateConfig(name, updates) {
      api("/" + name + "/config", {
        method: "PUT",
        body: JSON.stringify(updates),
      }).then(function (result) {
        setConfig(result.config);
        loadProfiles();
      });
    }

    function scanProfile(name) {
      setLoading(true);
      api("/" + name + "/scan", { method: "POST" }).then(function () {
        return loadProfileData(name);
      }).catch(function (err) {
        console.error("Scan failed:", err);
      }).finally(function () {
        setLoading(false);
      });
    }

    function manualSwitch(name, model, provider) {
      setLoading(true);
      api("/" + name + "/switch", {
        method: "POST",
        body: JSON.stringify({ model: model, provider: provider }),
      }).then(function () {
        return Promise.all([
          loadProfiles(),
          loadProfileData(name),
        ]);
      }).catch(function (err) {
        console.error("Switch failed:", err);
      }).finally(function () {
        setLoading(false);
      });
    }

    useEffect(loadProfiles, []);

    // Find selected profile data
    var selectedProfile = null;
    if (selected && profiles) {
      for (var i = 0; i < profiles.length; i++) {
        if (profiles[i].profile_name === selected) {
          selectedProfile = profiles[i];
          break;
        }
      }
    }

    var isOverridden = config && config.manual_override;
    var isAutoEnabled = config && config.auto_switch;

    return h("div", { style: { padding: "16px", color: "#c0caf5" } }, [

      // Header
      h("div", { style: { display: "flex", alignItems: "center", gap: "8px", marginBottom: "16px" } }, [
        h("h1", { style: { fontSize: "20px", fontWeight: 700, margin: 0 } }, "Auto-Switch"),
        h("span", { style: { fontSize: "12px", color: "#565f89", marginLeft: "8px" } }, "Model Switching Manager"),
        h("div", { style: { flex: 1 } }),
        h(Button, {
          variant: "outline", size: "sm",
          onClick: loadProfiles,
          disabled: loading,
        }, loading ? "Loading..." : "\u21BB Refresh"),
      ]),

      // Stats
      h(StatsCard, { stats: stats }),

      // Main area
      h("div", { style: { display: "grid", gridTemplateColumns: "300px 1fr", gap: "16px" } }, [

        // Left sidebar — profile list
        h(Card, { style: { overflow: "hidden" } },
          h(CardContent, { style: { padding: 0 } },
            profiles && profiles.length > 0
              ? profiles.map(function (p) {
                  return h(ProfileRow, {
                    key: p.profile_name,
                    profile: p,
                    isSelected: selected === p.profile_name,
                    onClick: function () { selectProfile(p.profile_name); },
                  });
                })
              : h("div", { style: { padding: "24px", textAlign: "center", color: "#565f89" } }, "No profiles found")
          )
        ),

        // Right content — selected profile details
        h("div", { style: { display: "flex", flexDirection: "column", gap: "12px" } }, [
          selected && h(Card, null, [
            h(CardHeader, { style: { padding: "12px 16px" } },
              h(CardTitle, { style: { fontSize: "16px", display: "flex", alignItems: "center", gap: "8px" } }, [
                h("span", null, "Config: " + selected),
                isOverridden && h(Badge, { variant: "warning" }, "Manual Override"),
                !isAutoEnabled && h(Badge, { variant: "secondary" }, "Auto Off"),
              ])
            ),
            h(ConfigPanel, {
              profile: selected,
              config: config,
              snapshot: snapshot,
              loading: loading,
              onUpdate: function (updates) { updateConfig(selected, updates); },
              onScan: function () { scanProfile(selected); },
            }),
          ]),

          selected && h(Card, null, [
            h(CardHeader, { style: { padding: "12px 16px" } },
              h(CardTitle, { style: { fontSize: "16px" } }, "Switch History")
            ),
            h(HistoryPanel, { history: history }),
          ]),

          !selected && h(Card, null,
            h(CardContent, { style: { padding: "48px", textAlign: "center", color: "#565f89" } },
              "Select a profile from the left to view and manage its model switching configuration."
            )
          ),
        ]),
      ]),
    ]);
  }

  // -----------------------------------------------------------------------
  // Register
  // -----------------------------------------------------------------------
  const PLUGINS = window.__HERMES_PLUGINS__;
  if (PLUGINS) {
    PLUGINS.register("provider-auto-switch", App);
  }
})();
