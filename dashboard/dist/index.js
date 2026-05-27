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
  const { useState, useEffect } = SDK.hooks;

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
  // API helpers — follow achievements plugin pattern
  // -----------------------------------------------------------------------
  const BASE = "/api/plugins/provider-auto-switch";

  async function api(path, options) {
    const url = BASE + path;
    const token = window.__HERMES_SESSION_TOKEN__ || "";
    const headers = { ...((options && options.headers) || {}) };
    if (token) headers["X-Hermes-Session-Token"] = token;
    if (options && options.body && typeof options.body === 'string') {
      headers["Content-Type"] = "application/json";
    }
    const res = await fetch(url, { ...(options || {}), headers });
    if (!res.ok) {
      const text = await res.text().catch(function () { return res.statusText; });
      throw new Error(res.status + ": " + text);
    }
    const text = await res.text();
    try { return JSON.parse(text); } catch (_) { return null; }
  }

  // -----------------------------------------------------------------------
  // StatusBadge
  // -----------------------------------------------------------------------
  function StatusBadge({ status, label }) {
    const color = STATUS_COLORS[status] || STATUS_COLORS.unknown;
    return h("span", { className: "psw-badge", style: { color: color } }, [
      h("span", { className: "psw-badge-dot", style: { backgroundColor: color } }),
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
      { label: "Auto", value: stats.auto_switch_on, color: "#9ece6a" },
      { label: "Manual", value: stats.manual_override, color: "#e0af68" },
    ];
    return h(Card, { style: { marginBottom: "12px" } },
      h(CardContent, { className: "psw-stats" },
        items.map(function (item) {
          return h("div", { key: item.label, className: "psw-stat-item" }, [
            h("div", { className: "psw-stat-value", style: { color: item.color } }, String(item.value)),
            h("div", { className: "psw-stat-label" }, item.label),
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
      className: "psw-profile-row",
      style: {
        borderLeftColor: isSelected ? "#7aa2f7" : "transparent",
        backgroundColor: isSelected ? "rgba(122,162,247,0.08)" : "transparent",
        padding: "8px 12px", cursor: "pointer",
        display: "flex", alignItems: "center", gap: "8px",
      },
    }, [
      h("span", { style: { fontWeight: 600, fontSize: "13px", color: "#c0caf5", flex: 1 } }, profile.profile_name),
      h(StatusBadge, { status: status, label: combo ? "active" : "n/a" }),
      h("span", { style: { color: "#565f89", fontSize: "10px" } }, isSelected ? "\u25B2" : "\u25BC"),
    ]);
  }

  // -----------------------------------------------------------------------
  // ConfigPanel
  // -----------------------------------------------------------------------
  function ConfigPanel({ profile, config, snapshot, activeCombo, manualOverrideEn, isAutoEnabled, onUpdate, loading }) {
    if (!config) return h("div", { style: { padding: "24px", textAlign: "center", color: "#565f89" } }, "No config yet — config will be auto-created on first use.");

    const [strategy, setStrategy] = React.useState(config.strategy);
    const [autoSwitch, setAutoSwitch] = React.useState(config.auto_switch);
    const [modelPrio, setModelPrio] = React.useState(config.model_priority || []);
    const [providerPrio, setProviderPrio] = React.useState(config.provider_priority || []);
    const [modelProviders, setModelProviders] = React.useState(config.model_providers || {});
    const [providerModels, setProviderModels] = React.useState(config.provider_models || {});

    useEffect(function () {
      setStrategy(config.strategy);
      setAutoSwitch(config.auto_switch);
      setModelPrio(config.model_priority || []);
      setProviderPrio(config.provider_priority || []);
      setModelProviders(config.model_providers || {});
      setProviderModels(config.provider_models || {});
    }, [config]);

    function save() {
      onUpdate({
        strategy: strategy,
        auto_switch: autoSwitch,
        model_priority: modelPrio,
        provider_priority: providerPrio,
        model_providers: strategy === "model_first" ? modelProviders : {},
        provider_models: strategy === "provider_first" ? providerModels : {},
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

    function moveSubItem(obj, model, idx, dir) {
      var list = (obj[model] || []).slice();
      var target = idx + dir;
      if (target < 0 || target >= list.length) return obj;
      var tmp = list[target];
      list[target] = list[idx];
      list[idx] = tmp;
      var next = {};
      Object.keys(obj).forEach(function (k) { next[k] = obj[k]; });
      next[model] = list;
      return next;
    }

    function addSubItem(obj, model, provider) {
      var list = (obj[model] || []).slice();
      if (list.indexOf(provider) !== -1) return obj;
      var next = {};
      Object.keys(obj).forEach(function (k) { next[k] = obj[k]; });
      next[model] = list.concat([provider]);
      return next;
    }

    function removeSubItem(obj, model, idx) {
      var list = (obj[model] || []).slice();
      list.splice(idx, 1);
      var next = {};
      Object.keys(obj).forEach(function (k) { next[k] = obj[k]; });
      next[model] = list;
      return next;
    }

    // Collect all known providers from the snapshot
    function allProviders() {
      var all = {};
      if (snapshot) {
        Object.keys(snapshot).forEach(function (m) { snapshot[m].forEach(function (e) { all[e.provider] = true; }); });
      }
      return Object.keys(all).sort();
    }

    // Provider sub-list for strategy=model_first
    function renderPerModelProviders(model) {
      var list = modelProviders[model] || [];
      var avail = allProviders();
      var unadded = avail.filter(function (p) { return list.indexOf(p) === -1; });

      return h("div", { style: { marginLeft: "20px", marginTop: "4px", marginBottom: "8px", padding: "6px 8px", backgroundColor: "rgba(86,95,137,0.08)", borderRadius: "4px" } }, [
        h("div", { style: { fontSize: "11px", color: "#565f89", marginBottom: "4px", fontWeight: 600 } }, "Provider priority for \"" + model + "\":"),
        list.length === 0
          ? h("span", { style: { fontSize: "11px", color: "#565f89", fontStyle: "italic" } }, "(falls back to global Provider Priority)")
          : h("div", { style: { display: "flex", flexDirection: "column", gap: "2px" } },
              list.map(function (p, pi) {
                return h("div", { key: p, style: { display: "flex", alignItems: "center", gap: "4px", fontSize: "12px" } }, [
                  h("span", { style: { flex: 1, color: "#a9b1d6" } }, String(pi + 1) + ". " + p),
                  h("button", {
                    onClick: function () { setModelProviders(moveSubItem(modelProviders, model, pi, -1)); },
                    disabled: pi === 0,
                    style: { cursor: pi === 0 ? "not-allowed" : "pointer", opacity: pi === 0 ? 0.3 : 1, border: "none", background: "none", color: "#7aa2f7", fontSize: "12px", padding: "0 2px" },
                  }, "\u25B2"),
                  h("button", {
                    onClick: function () { setModelProviders(moveSubItem(modelProviders, model, pi, 1)); },
                    disabled: pi === list.length - 1,
                    style: { cursor: pi === list.length - 1 ? "not-allowed" : "pointer", opacity: pi === list.length - 1 ? 0.3 : 1, border: "none", background: "none", color: "#7aa2f7", fontSize: "12px", padding: "0 2px" },
                  }, "\u25BC"),
                  h("button", {
                    onClick: function () { setModelProviders(removeSubItem(modelProviders, model, pi)); },
                    style: { border: "none", background: "none", color: "#f7768e", cursor: "pointer", fontSize: "12px", padding: "0 2px" },
                  }, "\u2715"),
                ]);
              })
            ),
        unadded.length > 0 && h("div", { style: { display: "flex", gap: "4px", marginTop: "4px" } }, [
          h("select", {
            id: "psw-add-mp-" + model,
            style: { flex: 1, fontSize: "11px", padding: "1px 4px", backgroundColor: "#1a1b26", color: "#c0caf5", border: "1px solid #565f89", borderRadius: "4px" },
          }, [
            h("option", { value: "", disabled: true, selected: true }, "+ add provider..."),
            unadded.map(function (p) { return h("option", { value: p }, p); }),
          ]),
          h("button", {
            onClick: function () {
              var sel = document.getElementById("psw-add-mp-" + model);
              var val = sel && sel.value;
              if (val) setModelProviders(addSubItem(modelProviders, model, val));
            },
            style: { fontSize: "11px", padding: "1px 6px", cursor: "pointer", backgroundColor: "#7aa2f7", color: "#fff", border: "none", borderRadius: "4px" },
          }, "Add"),
        ]),
      ]);
    }

    // Model sub-list for strategy=provider_first
    function renderPerProviderModels(provider) {
      var list = providerModels[provider] || [];
      var avail = Object.keys(snapshot || {}).sort();
      var unadded = avail.filter(function (m) { return list.indexOf(m) === -1; });

      return h("div", { style: { marginLeft: "20px", marginTop: "4px", marginBottom: "8px", padding: "6px 8px", backgroundColor: "rgba(86,95,137,0.08)", borderRadius: "4px" } }, [
        h("div", { style: { fontSize: "11px", color: "#565f89", marginBottom: "4px", fontWeight: 600 } }, "Model priority for \"" + provider + "\":"),
        list.length === 0
          ? h("span", { style: { fontSize: "11px", color: "#565f89", fontStyle: "italic" } }, "(falls back to global Model Priority)")
          : h("div", { style: { display: "flex", flexDirection: "column", gap: "2px" } },
              list.map(function (m, mi) {
                return h("div", { key: m, style: { display: "flex", alignItems: "center", gap: "4px", fontSize: "12px" } }, [
                  h("span", { style: { flex: 1, color: "#a9b1d6" } }, String(mi + 1) + ". " + m),
                  h("button", {
                    onClick: function () { setProviderModels(moveSubItem(providerModels, provider, mi, -1)); },
                    disabled: mi === 0,
                    style: { cursor: mi === 0 ? "not-allowed" : "pointer", opacity: mi === 0 ? 0.3 : 1, border: "none", background: "none", color: "#7aa2f7", fontSize: "12px", padding: "0 2px" },
                  }, "\u25B2"),
                  h("button", {
                    onClick: function () { setProviderModels(moveSubItem(providerModels, provider, mi, 1)); },
                    disabled: mi === list.length - 1,
                    style: { cursor: mi === list.length - 1 ? "not-allowed" : "pointer", opacity: mi === list.length - 1 ? 0.3 : 1, border: "none", background: "none", color: "#7aa2f7", fontSize: "12px", padding: "0 2px" },
                  }, "\u25BC"),
                  h("button", {
                    onClick: function () { setProviderModels(removeSubItem(providerModels, provider, mi)); },
                    style: { border: "none", background: "none", color: "#f7768e", cursor: "pointer", fontSize: "12px", padding: "0 2px" },
                  }, "\u2715"),
                ]);
              })
            ),
        unadded.length > 0 && h("div", { style: { display: "flex", gap: "4px", marginTop: "4px" } }, [
          h("select", {
            id: "psw-add-pm-" + provider,
            style: { flex: 1, fontSize: "11px", padding: "1px 4px", backgroundColor: "#1a1b26", color: "#c0caf5", border: "1px solid #565f89", borderRadius: "4px" },
          }, [
            h("option", { value: "", disabled: true, selected: true }, "+ add model..."),
            unadded.map(function (m) { return h("option", { value: m }, m); }),
          ]),
          h("button", {
            onClick: function () {
              var sel = document.getElementById("psw-add-pm-" + provider);
              var val = sel && sel.value;
              if (val) setProviderModels(addSubItem(providerModels, provider, val));
            },
            style: { fontSize: "11px", padding: "1px 6px", cursor: "pointer", backgroundColor: "#7aa2f7", color: "#fff", border: "none", borderRadius: "4px" },
          }, "Add"),
        ]),
      ]);
    }

    function renderPriorityList(items, setItems, renderSub) {
      return h("div", { style: { display: "flex", flexDirection: "column", gap: "4px" } },
        items.map(function (item, idx) {
          return h("div", { key: item }, [
            h("div", { className: "psw-priority-item" }, [
              h("span", { className: "psw-priority-label" }, item),
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
            ]),
            // Per-item sub-list (model_providers or provider_models)
            renderSub && renderSub(item),
          ]);
        })
      );
    }

    return h("div", { className: "psw-config-section" }, [
      // Current model indicator
      h("div", {
        style: {
          display: "flex", alignItems: "center", gap: "12px",
          padding: "8px 12px", marginBottom: "12px",
          background: "rgba(122,162,247,0.06)", borderRadius: "6px",
          border: "1px solid rgba(122,162,247,0.12)",
        }
      }, [
        h("span", { style: { fontSize: "11px", color: "#565f89", fontWeight: 600 } }, "CURRENT"),
        h("span", {
          style: { fontSize: "16px", fontWeight: 700, color: "#9ece6a" }
        }, activeCombo && activeCombo.model_name ? activeCombo.model_name : "—"),
        h("span", {
          style: { fontSize: "12px", color: "#a9b1d6" }
        }, activeCombo && activeCombo.provider_name ? "@ " + activeCombo.provider_name : ""),
        h("div", { style: { flex: 1 } }),
        h("span", {
          style: {
            fontSize: "11px", padding: "2px 8px", borderRadius: "10px",
            background: isAutoEnabled ? "rgba(158,206,106,0.15)" : "rgba(86,95,137,0.2)",
            color: isAutoEnabled ? "#9ece6a" : "#565f89",
            fontWeight: 600,
          }
        }, isAutoEnabled ? "Auto ✓" : "Auto ✗"),
        manualOverrideEn &&
          h("span", {
            style: { fontSize: "11px", padding: "2px 8px", borderRadius: "10px",
                     background: "rgba(224,175,104,0.15)", color: "#e0af68", fontWeight: 600 }
          }, "Manual"),
      ]),

      // Strategy + Toggles
      h("div", { className: "psw-config-toolbar" }, [
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
          variant: "primary", size: "sm",
          onClick: save,
        }, "Save Config"),
      ]),

      // Priority Lists
      h("div", { className: "psw-priority-grid" }, [
        h("div", null, [
          h("div", { className: "psw-priority-title" }, "Model Priority"),
          renderPriorityList(modelPrio, setModelPrio, strategy === "model_first" ? renderPerModelProviders : null),
          // Add-model dropdown from snapshot
          snapshot && h("div", { style: { display: "flex", gap: "4px", marginTop: "6px" } }, [
            h("select", {
              id: "psw-add-model-" + profile,
              style: { flex: 1, fontSize: "12px", padding: "2px 4px", backgroundColor: "#1a1b26", color: "#c0caf5", border: "1px solid #565f89", borderRadius: "4px" },
            }, [
              h("option", { value: "", disabled: true, selected: true }, "+ add model..."),
              Object.keys(snapshot).sort().filter(function (m) { return modelPrio.indexOf(m) === -1; }).map(function (m) {
                return h("option", { value: m }, m);
              }),
            ]),
            h("button", {
              onClick: function () {
                var sel = document.getElementById("psw-add-model-" + profile);
                var val = sel && sel.value;
                if (val && modelPrio.indexOf(val) === -1) setModelPrio(modelPrio.concat([val]));
              },
              style: { fontSize: "12px", padding: "2px 8px", cursor: "pointer", backgroundColor: "#7aa2f7", color: "#fff", border: "none", borderRadius: "4px" },
            }, "Add"),
          ]),
        ]),
        h("div", null, [
          h("div", { style: { fontSize: "12px", fontWeight: 600, color: "#565f89", marginBottom: "6px" } }, "Provider Priority"),
          renderPriorityList(providerPrio, setProviderPrio, strategy === "provider_first" ? renderPerProviderModels : null),
          // Add-provider dropdown from snapshot
          snapshot && h("div", { style: { display: "flex", gap: "4px", marginTop: "6px" } }, [
            h("select", {
              id: "psw-add-prov-" + profile,
              style: { flex: 1, fontSize: "12px", padding: "2px 4px", backgroundColor: "#1a1b26", color: "#c0caf5", border: "1px solid #565f89", borderRadius: "4px" },
            }, [
              h("option", { value: "", disabled: true, selected: true }, "+ add provider..."),
              (function () {
                var all = {};
                Object.keys(snapshot).forEach(function (m) { snapshot[m].forEach(function (e) { all[e.provider] = true; }); });
                return Object.keys(all).sort().filter(function (p) { return providerPrio.indexOf(p) === -1; }).map(function (p) {
                  return h("option", { value: p }, p);
                });
              })(),
            ]),
            h("button", {
              onClick: function () {
                var sel = document.getElementById("psw-add-prov-" + profile);
                var val = sel && sel.value;
                if (val && providerPrio.indexOf(val) === -1) setProviderPrio(providerPrio.concat([val]));
              },
              style: { fontSize: "12px", padding: "2px 8px", cursor: "pointer", backgroundColor: "#7aa2f7", color: "#fff", border: "none", borderRadius: "4px" },
            }, "Add"),
          ]),
        ]),
      ]),

      // Switch History inline (compact)
    ]);  // end return
  }  // end ConfigPanel

  // -----------------------------------------------------------------------
  // HistoryPanel
  // -----------------------------------------------------------------------
  function HistoryPanel({ history }) {
    if (!history || history.length === 0) {
      return h("div", { style: { padding: "12px 20px", fontSize: "13px", color: "#565f89" } }, "No switch history yet.");
    }
    return h("div", { className: "psw-table-wrap" },
      h("table", { className: "psw-table" }, [
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
        // Config: flat response (no .config wrapper)
        setConfig(results[0]);
        // Snapshot: flat entries array → group by model ({model: [{provider, status, error_reason}, ...]})
        var grouped = {};
        if (results[1] && results[1].entries) {
          results[1].entries.forEach(function (e) {
            if (!grouped[e.model]) grouped[e.model] = [];
            grouped[e.model].push({
              provider: e.provider,
              status: e.status,
              last_available_at: e.last_available_at,
              error_reason: e.error_reason,
            });
          });
        }
        setSnapshot(grouped);
        // History: .entries array
        setHistory(results[2] && results[2].entries ? results[2].entries : []);
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
      }).then(function () {
        // Reload full profile data to refresh config state
        api("/" + name + "/config").then(function (c) { setConfig(c); });
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

    return h("div", { className: "psw-app" }, [

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
      h("div", { className: "psw-layout" }, [

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
              activeCombo: selectedProfile ? selectedProfile.active_combo : null,
              manualOverrideEn: isOverridden,
              isAutoEnabled: isAutoEnabled,
              loading: loading,
              onUpdate: function (updates) { updateConfig(selected, updates); },
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
