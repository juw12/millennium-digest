/* Millennium Lab - side panels, step bars and the one-minute refresh (re-reads lab_state.js, which
   lab.py rewrites every 30 minutes from research_queue.json and the agent's job list). */
(function () {
  "use strict";
  var arena = null;
  var selected = null;
  var REFRESH_MS = 60 * 1000;

  function $(id) {
    return document.getElementById(id);
  }
  function escapeHtml(value) {
    return String(value == null ? "" : value).replace(/[&<>"']/g, function (ch) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch];
    });
  }
  var helpers = window.LabArena || {
    moodColor: function () { return "#9a948a"; },
    segColor: function () { return "#2f5b8c"; },
    moodText: {},
    shorten: function (text) { return text; }
  };

  function moodDot(mood) {
    return '<span class="dot" style="background:' + helpers.moodColor(mood) + '"></span>';
  }

  function renderTop(state) {
    var steps = state.steps_today || 0;
    var target = state.target_per_day || 8;
    var working = (state.bots || []).filter(function (bot) { return bot.mood === "working"; }).length;
    var next = state.next_milestone;
    var chips = [
      '<span class="chip ' + (steps >= target ? "good" : steps ? "warn" : "") + '" id="chip-steps"><b>' + steps + " / " + target + "</b> steps today</span>",
      '<span class="chip"><b>' + (state.open_jobs || 0) + "</b> jobs open</span>",
      '<span class="chip ' + (working ? "good" : "") + '"><b>' + working + "</b> bots working now</span>"
    ];
    if (next) {
      chips.push('<span class="chip hot">Next: <b>' + escapeHtml(next.id) + "</b> in " + next.days_left + " days</span>");
    }
    $("chips").innerHTML = chips.join("");
    $("updated").textContent = "state " + String(state.generated || "").replace("T", " ").slice(0, 16);
    $("legend").innerHTML = ["working", "queued", "planning", "review", "idle"].map(function (mood) {
      return "<span>" + moodDot(mood) + mood + "</span>";
    }).join("");
  }

  function renderBars(state) {
    var days = state.steps_by_day || [];
    var target = state.target_per_day || 8;
    var peak = Math.max(target, Math.max.apply(null, days.map(function (d) { return d.steps; }).concat([1])));
    var html = days.map(function (day) {
      var date = new Date(day.day + "T12:00:00");
      var label = date.toLocaleDateString(undefined, { weekday: "short" });
      var height = Math.max(3, Math.round(52 * day.steps / peak));
      var classes = "bar" + (day.steps >= target ? " met" : "") + (day.day === state.today ? " today" : "");
      return '<div class="' + classes + '" title="' + escapeHtml(day.day) + ": " + day.steps + ' finished steps"><b>' + day.steps +
        '</b><i style="height:' + height + 'px"></i><small>' + escapeHtml(label) + "</small></div>";
    }).join("");
    var lineBottom = 16 + Math.round(52 * target / peak);
    $("bars").innerHTML = html + '<div class="target-line" style="bottom:' + lineBottom + 'px" title="target"></div>';
    $("strip-target").textContent = "target " + target + " a day (dashed line)";
  }

  function renderFocus(state) {
    var bots = state.bots || [];
    var bot = bots.filter(function (b) { return b.id === selected; })[0] || bots[0];
    if (!bot) {
      $("focus").innerHTML = "<p>No lab state yet. Run <code>python lab.py</code>.</p>";
      return;
    }
    var job = bot.job;
    var agent = job && job.agent ? job.agent.status + (job.agent.rounds ? " (round " + job.agent.rounds + ")" : "") : "not picked up yet";
    var rows = [
      ["Problem", escapeHtml(bot.problem)],
      ["Role", escapeHtml(bot.role)],
      ["Status", '<span class="mood">' + moodDot(bot.mood) + escapeHtml(helpers.moodText[bot.mood] || bot.mood) + "</span>"],
      ["Job", job ? escapeHtml(job.title) + '<br><span class="muted">' + escapeHtml(job.id) + "</span>" : "none open"],
      ["Agent", job ? escapeHtml(agent) : "-"],
      ["Finished", bot.done_today + " today &middot; " + bot.done_total + " total"],
      ["Last result", bot.last ? escapeHtml(bot.last.result || "(no result text)") : "none yet"]
    ];
    $("focus").innerHTML =
      '<div class="focus-head"><span class="avatar" style="background:' + helpers.segColor(bot.color) + '">' +
      escapeHtml(bot.bot.charAt(0)) + "</span><div><h2>" + escapeHtml(bot.bot) + '</h2><span class="muted">' +
      escapeHtml(bot.name) + "</span></div></div>" +
      '<dl class="kv">' + rows.map(function (row) { return "<dt>" + row[0] + "</dt><dd>" + row[1] + "</dd>"; }).join("") + "</dl>";
  }

  function renderDepartments(state) {
    var bots = state.bots || [];
    $("dept-count").textContent = bots.length + " bots";
    $("departments").innerHTML = bots.map(function (bot) {
      var sub = bot.job ? helpers.shorten(bot.job.title, 48) : helpers.moodText.idle || "idle";
      return '<button class="dept" data-bot="' + escapeHtml(bot.id) + '" aria-pressed="' + (bot.id === selected) + '">' +
        '<span class="swatch" style="background:' + helpers.segColor(bot.color) + '"></span>' +
        "<span><strong>" + escapeHtml(bot.bot) + " &middot; " + escapeHtml(bot.name) + "</strong>" +
        '<span class="sub">' + moodDot(bot.mood) + " " + escapeHtml(sub) + "</span></span>" +
        '<span class="count" title="finished today / total">' + bot.done_today + "/" + bot.done_total + "</span></button>";
    }).join("");
    Array.prototype.forEach.call(document.querySelectorAll(".dept"), function (button) {
      button.addEventListener("click", function () {
        choose(button.getAttribute("data-bot"));
      });
    });
  }

  function renderMilestones(state) {
    var nextId = state.next_milestone ? state.next_milestone.id : null;
    $("milestones").innerHTML = (state.milestones || []).map(function (m) {
      var classes = (m.done ? "done" : "") + (m.id === nextId ? " next" : "");
      var when = m.done ? "done" : m.days_left >= 0 ? m.days_left + " days" : Math.abs(m.days_left) + " days late";
      return '<li class="' + classes + '"><div class="due"><b>' + escapeHtml(m.due) + "</b>" + escapeHtml(when) + "</div>" +
        '<div><div class="title">' + escapeHtml(m.id) + " &middot; " + escapeHtml(m.title) + "</div>" +
        '<div class="note">' + escapeHtml(m.note) + "</div>" +
        '<div class="meter"><i style="width:' + Math.round(100 * (m.progress || 0)) + '%"></i></div></div></li>';
    }).join("");
    $("honest").textContent = state.not_on_timeline || "";
  }

  function render(state) {
    renderTop(state);
    renderBars(state);
    renderFocus(state);
    renderDepartments(state);
    renderMilestones(state);
  }

  function choose(id) {
    selected = id;
    if (arena) {
      arena.select(id);
    }
    renderFocus(window.LAB_STATE || {});
    renderDepartments(window.LAB_STATE || {});
  }

  function refresh() {
    var script = document.createElement("script");
    script.src = "lab_state.js?t=" + Date.now();
    script.onload = function () {
      script.remove();
      var state = window.LAB_STATE || {};
      render(state);
      if (arena) {
        arena.update(state);
      }
    };
    script.onerror = function () {
      script.remove();
    };
    document.head.appendChild(script);
  }

  function start() {
    var state = window.LAB_STATE || { bots: [], milestones: [] };
    var firstWorking = (state.bots || []).filter(function (bot) { return bot.mood === "working"; })[0];
    selected = (firstWorking || (state.bots || [])[0] || {}).id || null;
    render(state);
    if (window.LabArena && window.LabArena.supported() && (state.bots || []).length) {
      try {
        arena = window.LabArena.create($("stage"), state, choose);
        arena.select(selected);
        window.__labArena = arena;  // for the UI test
      } catch (error) {
        console.error(error);
        $("nowebgl").hidden = false;
      }
    } else {
      $("nowebgl").hidden = false;
    }
    setInterval(refresh, REFRESH_MS);
  }

  var fontsReady = document.fonts && document.fonts.ready ? document.fonts.ready : Promise.resolve();
  fontsReady.then(start, start);
})();
