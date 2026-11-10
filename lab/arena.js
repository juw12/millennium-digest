/* Millennium Lab - the 3D room. Classic script (the desktop app loads files over file://, where ES modules
   are blocked), so it uses the global build in vendor/three.global.js (three.js r160, MIT).
   window.LabArena.create(container, state, onSelect) -> arena with update(state) and select(id).
   Everything shown comes from lab_state.js; the bots only animate what the real jobs are doing. */
(function () {
  "use strict";
  var THREE = window.THREE;
  var TAU = Math.PI * 2;
  var REDUCED = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  function css(name, fallback) {
    var value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    return value || fallback;
  }
  function color(name, fallback) {
    return new THREE.Color(css(name, fallback));
  }
  var THEME = {
    bg: color("--scene-bg", "#f1ebdf"),
    floor: color("--scene-floor", "#e9e1d2"),
    plaza: color("--scene-plaza", "#f6f1e6"),
    path: color("--scene-path", "#ddd2bf"),
    desk: color("--scene-desk", "#b9946a"),
    deskTop: color("--scene-desk-top", "#8a5a2b"),
    leaf: color("--scene-leaf", "#5f8a4a"),
    stone: color("--scene-stone", "#d9cdb8"),
    head: color("--scene-head", "#fffdf8"),
    screenOff: color("--scene-screen-off", "#3a3832"),
    sky: color("--scene-sky", "#fff6e6"),
    ground: color("--scene-ground", "#c9b99d"),
    ink: color("--text", "#1b1a17"),
    accent: color("--accent", "#d9481f"),
    labelBg: css("--scene-label-bg", "rgba(255,253,248,0.97)"),
    labelFg: css("--scene-label-fg", "#1b1a17"),
    labelSub: css("--scene-label-sub", "#67625a"),
    line: css("--line", "#e1dbcf"),
    fontUi: css("--font-ui", "sans-serif"),
    fontDisplay: css("--font-display", "serif"),
    fontMono: css("--font-mono", "monospace")
  };
  var MOOD_TOKEN = {
    working: "--mood-working", queued: "--mood-queued", planning: "--mood-planning",
    review: "--mood-review", idle: "--mood-idle"
  };
  var MOOD_TEXT = {
    working: "working on it", queued: "waiting for the agent", planning: "planning the next step",
    review: "result needs review", idle: "idle"
  };
  function moodColor(mood) {
    return css(MOOD_TOKEN[mood] || MOOD_TOKEN.idle, "#9a948a");
  }
  function segColor(index) {
    return css("--seg-" + (index || 1), "#2f5b8c");
  }
  function shorten(text, length) {
    text = String(text || "").replace(/\s*\[\d{4}-\d{2}-\d{2}\]\s*$/, "");
    return text.length > length ? text.slice(0, length - 1) + "\u2026" : text;
  }

  // ------------------------------------------------------------------ canvas sprites
  function roundRect(ctx, x, y, w, h, r) {
    ctx.beginPath();
    ctx.moveTo(x + r, y);
    ctx.arcTo(x + w, y, x + w, y + h, r);
    ctx.arcTo(x + w, y + h, x, y + h, r);
    ctx.arcTo(x, y + h, x, y, r);
    ctx.arcTo(x, y, x + w, y, r);
    ctx.closePath();
  }
  function textureFrom(canvas) {
    var texture = new THREE.CanvasTexture(canvas);
    texture.colorSpace = THREE.SRGBColorSpace;
    texture.anisotropy = 4;
    return texture;
  }
  /* A label card: title line, optional sub line, optional colour swatch, optional speech tail. */
  function cardCanvas(title, sub, swatch, tail) {
    var scale = 2;
    var canvas = document.createElement("canvas");
    var ctx = canvas.getContext("2d");
    var titleFont = "600 " + 15 * scale + "px " + THEME.fontUi;
    var subFont = 13 * scale + "px " + THEME.fontUi;
    ctx.font = titleFont;
    var width = ctx.measureText(title).width;
    if (sub) {
      ctx.font = subFont;
      width = Math.max(width, ctx.measureText(sub).width);
    }
    var pad = 11 * scale;
    var swatchWidth = swatch ? 12 * scale : 0;
    var tailHeight = tail ? 9 * scale : 0;
    var boxHeight = (sub ? 46 : 30) * scale;
    canvas.width = Math.ceil(width + pad * 2 + swatchWidth);
    canvas.height = boxHeight + tailHeight + 4;
    ctx = canvas.getContext("2d");
    ctx.fillStyle = THEME.labelBg;
    ctx.strokeStyle = THEME.line;
    ctx.lineWidth = 2;
    roundRect(ctx, 1, 1, canvas.width - 2, boxHeight, 9 * scale);
    ctx.fill();
    ctx.stroke();
    if (tail) {
      ctx.beginPath();
      ctx.moveTo(canvas.width / 2 - 8 * scale, boxHeight - 1);
      ctx.lineTo(canvas.width / 2, boxHeight + tailHeight);
      ctx.lineTo(canvas.width / 2 + 8 * scale, boxHeight - 1);
      ctx.closePath();
      ctx.fill();
    }
    if (swatch) {
      ctx.fillStyle = swatch;
      roundRect(ctx, pad, 9 * scale, 5 * scale, boxHeight - 18 * scale, 2 * scale);
      ctx.fill();
    }
    ctx.textBaseline = "middle";
    ctx.fillStyle = THEME.labelFg;
    ctx.font = titleFont;
    ctx.fillText(title, pad + swatchWidth, sub ? 16 * scale : boxHeight / 2);
    if (sub) {
      ctx.fillStyle = THEME.labelSub;
      ctx.font = subFont;
      ctx.fillText(sub, pad + swatchWidth, 33 * scale);
    }
    return canvas;
  }
  function makeSprite(canvas, worldHeight) {
    var material = new THREE.SpriteMaterial({ map: textureFrom(canvas), depthWrite: false, transparent: true });
    var sprite = new THREE.Sprite(material);
    sprite.scale.set(worldHeight * canvas.width / canvas.height, worldHeight, 1);
    sprite.renderOrder = 10;
    return sprite;
  }
  function replaceSprite(sprite, canvas, worldHeight) {
    sprite.material.map.dispose();
    sprite.material.map = textureFrom(canvas);
    sprite.material.needsUpdate = true;
    sprite.scale.set(worldHeight * canvas.width / canvas.height, worldHeight, 1);
  }

  /* The central board: steps today vs target and the next milestone. */
  function boardCanvas(state) {
    var canvas = document.createElement("canvas");
    canvas.width = 512;
    canvas.height = 600;
    var ctx = canvas.getContext("2d");
    ctx.fillStyle = css("--panel", "#fffdf8");
    ctx.fillRect(0, 0, 512, 600);
    ctx.fillStyle = css("--accent", "#d9481f");
    ctx.fillRect(0, 0, 512, 14);
    ctx.fillStyle = THEME.labelSub;
    ctx.font = "500 26px " + THEME.fontUi;
    ctx.fillText("FINISHED STEPS TODAY", 36, 70);
    ctx.fillStyle = THEME.labelFg;
    ctx.font = "150px " + THEME.fontDisplay;
    var steps = state.steps_today || 0;
    var target = state.target_per_day || 8;
    ctx.fillText(String(steps), 36, 220);
    var stepsWidth = ctx.measureText(String(steps)).width;
    ctx.fillStyle = THEME.labelSub;
    ctx.font = "64px " + THEME.fontDisplay;
    ctx.fillText("/ " + target, 50 + stepsWidth, 220);
    ctx.fillStyle = css("--sunken", "#ebe6db");
    roundRect(ctx, 36, 250, 440, 18, 9);
    ctx.fill();
    ctx.fillStyle = steps >= target ? css("--buy", "#2b6e47") : css("--accent", "#d9481f");
    roundRect(ctx, 36, 250, Math.max(18, 440 * Math.min(1, steps / target)), 18, 9);
    ctx.fill();
    ctx.fillStyle = THEME.labelSub;
    ctx.font = "500 24px " + THEME.fontUi;
    ctx.fillText((state.open_jobs || 0) + " department jobs open", 36, 312);
    var next = state.next_milestone;
    ctx.strokeStyle = THEME.line;
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(36, 350);
    ctx.lineTo(476, 350);
    ctx.stroke();
    ctx.fillStyle = THEME.labelSub;
    ctx.font = "500 24px " + THEME.fontUi;
    ctx.fillText("NEXT MILESTONE", 36, 396);
    if (next) {
      ctx.fillStyle = THEME.labelFg;
      ctx.font = "44px " + THEME.fontDisplay;
      ctx.fillText(next.days_left + " days \u00b7 " + next.due, 36, 448);
      ctx.font = "24px " + THEME.fontUi;
      wrapText(ctx, next.title, 36, 490, 440, 30, 3);
    }
    return canvas;
  }
  function wrapText(ctx, text, x, y, maxWidth, lineHeight, maxLines) {
    var words = String(text).split(/\s+/);
    var line = "";
    var lines = 0;
    for (var i = 0; i < words.length; i++) {
      var test = line ? line + " " + words[i] : words[i];
      if (ctx.measureText(test).width > maxWidth && line) {
        lines += 1;
        if (lines === maxLines) {
          ctx.fillText(line + "\u2026", x, y);
          return;
        }
        ctx.fillText(line, x, y);
        line = words[i];
        y += lineHeight;
      } else {
        line = test;
      }
    }
    if (line) {
      ctx.fillText(line, x, y);
    }
  }

  // ------------------------------------------------------------------ scene pieces
  function standard(colorValue, extra) {
    var options = { color: colorValue, roughness: 0.85, metalness: 0.0 };
    for (var key in extra || {}) {
      options[key] = extra[key];
    }
    return new THREE.MeshStandardMaterial(options);
  }
  function shadowed(mesh) {
    mesh.castShadow = true;
    mesh.receiveShadow = true;
    return mesh;
  }

  function buildDesk(bot) {
    var group = new THREE.Group();
    var top = shadowed(new THREE.Mesh(new THREE.BoxGeometry(2.2, 0.12, 1.1), standard(THEME.deskTop)));
    top.position.y = 0.92;
    group.add(top);
    var legGeometry = new THREE.BoxGeometry(0.1, 0.9, 0.9);
    [-1, 1].forEach(function (side) {
      var leg = shadowed(new THREE.Mesh(legGeometry, standard(THEME.desk)));
      leg.position.set(side * 1.0, 0.45, 0);
      group.add(leg);
    });
    var stripe = new THREE.Mesh(new THREE.BoxGeometry(2.21, 0.05, 1.11), standard(new THREE.Color(segColor(bot.color))));
    stripe.position.y = 0.99;
    group.add(stripe);
    var monitor = shadowed(new THREE.Mesh(new THREE.BoxGeometry(1.1, 0.7, 0.07), standard(THEME.ink, { roughness: 0.5 })));
    monitor.position.set(0, 1.45, -0.15);
    group.add(monitor);
    var screen = new THREE.Mesh(
      new THREE.PlaneGeometry(1.0, 0.6),
      new THREE.MeshStandardMaterial({ color: THEME.screenOff, emissive: THEME.screenOff, emissiveIntensity: 0.6 })
    );
    screen.position.set(0, 1.45, -0.11);
    group.add(screen);
    var stand = new THREE.Mesh(new THREE.BoxGeometry(0.12, 0.4, 0.06), standard(THEME.ink));
    stand.position.set(0, 1.12, -0.18);
    group.add(stand);
    var paper = new THREE.Mesh(new THREE.BoxGeometry(0.45, 0.02, 0.32), standard(THEME.head));
    paper.position.set(0.7, 0.99, 0.15);
    paper.rotation.y = 0.3;
    group.add(paper);
    var dataBits = [];
    for (var i = 0; i < 3; i++) {
      var bit = new THREE.Mesh(new THREE.BoxGeometry(0.09, 0.09, 0.09), standard(new THREE.Color(segColor(bot.color)), { emissive: new THREE.Color(segColor(bot.color)), emissiveIntensity: 0.5 }));
      bit.visible = false;
      group.add(bit);
      dataBits.push(bit);
    }
    group.userData = { screen: screen, dataBits: dataBits };
    return group;
  }

  function buildBot(bot) {
    var group = new THREE.Group();
    var bodyColor = new THREE.Color(segColor(bot.color));
    var body = shadowed(new THREE.Mesh(new THREE.CapsuleGeometry(0.34, 0.55, 6, 16), standard(bodyColor, { roughness: 0.6 })));
    body.position.y = 0.72;
    group.add(body);
    var head = shadowed(new THREE.Mesh(new THREE.SphereGeometry(0.3, 24, 16), standard(THEME.head, { roughness: 0.5 })));
    head.position.y = 1.42;
    group.add(head);
    var visor = new THREE.Mesh(new THREE.BoxGeometry(0.36, 0.11, 0.05), standard(THEME.ink, { roughness: 0.3 }));
    visor.position.set(0, 1.45, 0.27);
    group.add(visor);
    var eyeMaterial = new THREE.MeshBasicMaterial({ color: new THREE.Color(moodColor(bot.mood)) });
    [-0.08, 0.08].forEach(function (x) {
      var eye = new THREE.Mesh(new THREE.SphereGeometry(0.03, 10, 8), eyeMaterial);
      eye.position.set(x, 1.45, 0.3);
      group.add(eye);
    });
    var antenna = new THREE.Mesh(new THREE.CylinderGeometry(0.015, 0.015, 0.22, 6), standard(THEME.ink));
    antenna.position.y = 1.82;
    group.add(antenna);
    var lamp = new THREE.Mesh(new THREE.SphereGeometry(0.06, 12, 8), new THREE.MeshBasicMaterial({ color: new THREE.Color(moodColor(bot.mood)) }));
    lamp.position.y = 1.95;
    group.add(lamp);
    var arms = [];
    [-1, 1].forEach(function (side) {
      var arm = shadowed(new THREE.Mesh(new THREE.CapsuleGeometry(0.07, 0.32, 4, 8), standard(bodyColor, { roughness: 0.6 })));
      arm.position.set(side * 0.42, 0.85, 0.05);
      group.add(arm);
      arms.push(arm);
    });
    var badge = new THREE.Mesh(new THREE.CircleGeometry(0.1, 16), new THREE.MeshBasicMaterial({ color: THEME.head }));
    badge.position.set(0, 0.95, 0.345);
    group.add(badge);
    group.userData = { body: body, head: head, lamp: lamp, eyes: eyeMaterial, arms: arms };
    [body, head, visor].forEach(function (mesh) {
      mesh.userData.botId = bot.id;
    });
    return group;
  }

  function buildTree(x, z, size) {
    var tree = new THREE.Group();
    var trunk = shadowed(new THREE.Mesh(new THREE.CylinderGeometry(0.09 * size, 0.12 * size, 0.9 * size, 8), standard(THEME.deskTop)));
    trunk.position.y = 0.45 * size;
    tree.add(trunk);
    var crown = shadowed(new THREE.Mesh(new THREE.IcosahedronGeometry(0.62 * size, 0), standard(THEME.leaf, { flatShading: true })));
    crown.position.y = 1.25 * size;
    tree.add(crown);
    tree.position.set(x, 0, z);
    return tree;
  }

  // ------------------------------------------------------------------ the arena
  function Arena(container, state, onSelect) {
    this.container = container;
    this.onSelect = onSelect || function () {};
    this.state = state;
    this.bots = {};
    this.pickable = [];
    this.selected = null;
    this.frames = 0;
    this.clock = new THREE.Clock();
    this.view = { radius: 23, theta: 0.6, phi: 0.98, target: new THREE.Vector3(0, 0.6, 0) };
    this.lastInteraction = -10;

    var renderer = new THREE.WebGLRenderer({ antialias: true, preserveDrawingBuffer: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    renderer.shadowMap.enabled = true;
    renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    renderer.setClearColor(THEME.bg);
    container.appendChild(renderer.domElement);
    this.renderer = renderer;

    var scene = new THREE.Scene();
    scene.background = THEME.bg;
    scene.fog = new THREE.Fog(THEME.bg, 34, 70);
    this.scene = scene;
    this.camera = new THREE.PerspectiveCamera(38, 1, 0.1, 200);

    scene.add(new THREE.HemisphereLight(THEME.sky, THEME.ground, 1.5));
    var sun = new THREE.DirectionalLight(0xffffff, 1.6);
    sun.position.set(9, 16, 7);
    sun.castShadow = true;
    sun.shadow.mapSize.set(2048, 2048);
    sun.shadow.camera.left = -16;
    sun.shadow.camera.right = 16;
    sun.shadow.camera.top = 16;
    sun.shadow.camera.bottom = -16;
    sun.shadow.bias = -0.0005;
    scene.add(sun);

    this.buildRoom();
    this.buildBoard(state);
    this.buildDepartments(state);
    this.bindControls();
    this.resize();
    var self = this;
    if (window.ResizeObserver) {
      new ResizeObserver(function () {
        self.resize();
      }).observe(container);
    } else {
      window.addEventListener("resize", function () {
        self.resize();
      });
    }
    this.loop = this.loop.bind(this);
    requestAnimationFrame(this.loop);
  }

  Arena.prototype.buildRoom = function () {
    var floor = new THREE.Mesh(new THREE.CircleGeometry(40, 64), standard(THEME.floor));
    floor.rotation.x = -Math.PI / 2;
    floor.receiveShadow = true;
    this.scene.add(floor);
    var plaza = new THREE.Mesh(new THREE.CircleGeometry(11.2, 64), standard(THEME.plaza));
    plaza.rotation.x = -Math.PI / 2;
    plaza.position.y = 0.01;
    plaza.receiveShadow = true;
    this.scene.add(plaza);
    var ring = new THREE.Mesh(new THREE.RingGeometry(3.0, 3.35, 64), standard(THEME.path));
    ring.rotation.x = -Math.PI / 2;
    ring.position.y = 0.02;
    this.scene.add(ring);
    var count = (this.state.bots || []).length || 8;
    for (var i = 0; i < count; i++) {
      var angle = (i + 0.5) * TAU / count;
      var path = new THREE.Mesh(new THREE.PlaneGeometry(0.5, 5.2), standard(THEME.path));
      path.rotation.x = -Math.PI / 2;
      path.rotation.z = -angle;
      path.position.set(Math.sin(angle) * 6.0, 0.015, Math.cos(angle) * 6.0);
      this.scene.add(path);
    }
    var treeSpots = [[13, 3, 1.2], [-12, 6, 1.0], [4, -13, 1.3], [-6, -12, 0.9], [11, -8, 1.0], [-13, -3, 1.2], [7, 12, 1.1], [-4, 13, 1.0]];
    var scene = this.scene;
    treeSpots.forEach(function (spot) {
      scene.add(buildTree(spot[0], spot[1], spot[2]));
    });
    for (var s = 0; s < 14; s++) {
      var a = s * 2.39;
      var r = 12 + (s % 3) * 1.4;
      var stone = shadowed(new THREE.Mesh(new THREE.DodecahedronGeometry(0.22 + (s % 4) * 0.07, 0), standard(THEME.stone, { flatShading: true })));
      stone.position.set(Math.sin(a) * r, 0.12, Math.cos(a) * r);
      scene.add(stone);
    }
  };

  Arena.prototype.buildBoard = function (state) {
    var group = new THREE.Group();
    var base = shadowed(new THREE.Mesh(new THREE.CylinderGeometry(1.5, 1.7, 0.3, 32), standard(THEME.stone)));
    base.position.y = 0.15;
    group.add(base);
    this.boardTexture = textureFrom(boardCanvas(state));
    var faceMaterial = new THREE.MeshStandardMaterial({ map: this.boardTexture, roughness: 0.9 });
    var sideMaterial = standard(THEME.ink, { roughness: 0.6 });
    for (var i = 0; i < 4; i++) {
      var pillar = new THREE.Group();
      var back = shadowed(new THREE.Mesh(new THREE.BoxGeometry(1.75, 2.1, 0.08), sideMaterial));
      back.position.set(0, 1.55, 0.86);
      pillar.add(back);
      var face = new THREE.Mesh(new THREE.PlaneGeometry(1.6, 1.875), faceMaterial);
      face.position.set(0, 1.55, 0.905);
      pillar.add(face);
      pillar.rotation.y = i * Math.PI / 2 + Math.PI / 4;
      group.add(pillar);
    }
    var cap = shadowed(new THREE.Mesh(new THREE.BoxGeometry(1.9, 0.1, 1.9), standard(THEME.accent)));
    cap.position.y = 2.65;
    cap.rotation.y = Math.PI / 4;
    group.add(cap);
    group.scale.setScalar(1.35);
    this.scene.add(group);
    this.board = group;
  };

  Arena.prototype.buildDepartments = function (state) {
    var bots = state.bots || [];
    var radius = 8.2;
    var self = this;
    bots.forEach(function (bot, index) {
      var angle = index * TAU / bots.length;
      var outward = new THREE.Vector3(Math.sin(angle), 0, Math.cos(angle));
      var desk = buildDesk(bot);
      desk.position.copy(outward.clone().multiplyScalar(radius));
      desk.rotation.y = angle + Math.PI;  // monitor faces outward, towards the seat
      self.scene.add(desk);
      desk.traverse(function (child) {
        if (child.isMesh) {
          child.userData.botId = bot.id;
          self.pickable.push(child);
        }
      });
      var label = makeSprite(cardCanvas(bot.name, bot.bot + " \u00b7 " + shorten(bot.problem, 34), segColor(bot.color), false), 0.62);
      label.position.copy(desk.position).add(new THREE.Vector3(0, 2.55, 0));
      self.scene.add(label);

      var figure = buildBot(bot);
      var seat = outward.clone().multiplyScalar(radius + 1.25);
      figure.position.copy(seat);
      figure.rotation.y = angle + Math.PI;
      self.scene.add(figure);
      figure.traverse(function (child) {
        if (child.isMesh && child.userData.botId) {
          self.pickable.push(child);
        }
      });
      var bubble = makeSprite(cardCanvas("...", null, null, true), 0.5);
      bubble.visible = false;
      self.scene.add(bubble);
      var halo = new THREE.Mesh(new THREE.RingGeometry(0.55, 0.68, 40), new THREE.MeshBasicMaterial({ color: THEME.accent, transparent: true, opacity: 0.9 }));
      halo.rotation.x = -Math.PI / 2;
      halo.visible = false;
      self.scene.add(halo);
      self.bots[bot.id] = {
        data: bot, angle: angle, outward: outward, seat: seat, desk: desk, figure: figure, bubble: bubble,
        halo: halo, label: label, target: seat.clone(), pause: 0, phase: index * 0.9, bubbleText: ""
      };
      self.applyMood(self.bots[bot.id]);
    });
  };

  Arena.prototype.applyMood = function (entry) {
    var bot = entry.data;
    var mood = bot.mood || "idle";
    var moodValue = new THREE.Color(moodColor(mood));
    entry.figure.userData.lamp.material.color.copy(moodValue);
    entry.figure.userData.eyes.color.copy(moodValue);
    var screen = entry.desk.userData.screen.material;
    if (mood === "idle") {
      screen.color.copy(THEME.screenOff);
      screen.emissive.copy(THEME.screenOff);
      screen.emissiveIntensity = 0.4;
    } else {
      screen.color.copy(moodValue);
      screen.emissive.copy(moodValue);
      screen.emissiveIntensity = mood === "working" ? 0.9 : 0.5;
    }
    var text = bot.job ? shorten(bot.job.title, 46) : MOOD_TEXT.idle;
    var title = MOOD_TEXT[mood] || mood;
    var key = title + "|" + text;
    if (key !== entry.bubbleText) {
      replaceSprite(entry.bubble, cardCanvas(title.charAt(0).toUpperCase() + title.slice(1), text, moodColor(mood), true), 0.62);
      entry.bubbleText = key;
    }
    if (mood !== "planning" && mood !== "idle") {
      entry.target.copy(entry.seat);
    }
  };

  Arena.prototype.update = function (state) {
    this.state = state;
    var self = this;
    (state.bots || []).forEach(function (bot) {
      var entry = self.bots[bot.id];
      if (entry) {
        entry.data = bot;
        self.applyMood(entry);
      }
    });
    var canvas = boardCanvas(state);
    this.boardTexture.image = canvas;
    this.boardTexture.needsUpdate = true;
  };

  Arena.prototype.select = function (id) {
    this.selected = id;
    var self = this;
    Object.keys(this.bots).forEach(function (key) {
      self.bots[key].halo.visible = key === id;
    });
  };

  // ------------------------------------------------------------------ controls
  Arena.prototype.bindControls = function () {
    var element = this.renderer.domElement;
    var self = this;
    var drag = null;
    element.addEventListener("pointerdown", function (event) {
      drag = { x: event.clientX, y: event.clientY, startX: event.clientX, startY: event.clientY };
      element.setPointerCapture(event.pointerId);
      self.lastInteraction = self.clock.elapsedTime;
    });
    element.addEventListener("pointermove", function (event) {
      if (!drag) {
        self.hover(event);
        return;
      }
      self.view.theta -= (event.clientX - drag.x) * 0.006;
      self.view.phi = Math.min(1.35, Math.max(0.35, self.view.phi - (event.clientY - drag.y) * 0.004));
      drag.x = event.clientX;
      drag.y = event.clientY;
      self.lastInteraction = self.clock.elapsedTime;
    });
    element.addEventListener("pointerup", function (event) {
      if (drag && Math.abs(event.clientX - drag.startX) + Math.abs(event.clientY - drag.startY) < 6) {
        self.pick(event);
      }
      drag = null;
    });
    element.addEventListener("wheel", function (event) {
      event.preventDefault();
      self.view.radius = Math.min(42, Math.max(9, self.view.radius * (1 + Math.sign(event.deltaY) * 0.08)));
      self.lastInteraction = self.clock.elapsedTime;
    }, { passive: false });
  };

  Arena.prototype.raycast = function (event) {
    var rect = this.renderer.domElement.getBoundingClientRect();
    var pointer = new THREE.Vector2(
      ((event.clientX - rect.left) / rect.width) * 2 - 1,
      -((event.clientY - rect.top) / rect.height) * 2 + 1
    );
    var caster = new THREE.Raycaster();
    caster.setFromCamera(pointer, this.camera);
    var hits = caster.intersectObjects(this.pickable, false);
    return hits.length ? hits[0].object.userData.botId : null;
  };
  Arena.prototype.pick = function (event) {
    var id = this.raycast(event);
    if (id) {
      this.select(id);
      this.onSelect(id);
    }
  };
  Arena.prototype.hover = function (event) {
    this.renderer.domElement.style.cursor = this.raycast(event) ? "pointer" : "";
  };

  Arena.prototype.resize = function () {
    var width = this.container.clientWidth || 800;
    var height = this.container.clientHeight || 500;
    this.renderer.setSize(width, height, false);
    this.camera.aspect = width / height;
    this.camera.updateProjectionMatrix();
  };

  // ------------------------------------------------------------------ animation
  Arena.prototype.animateBot = function (entry, time, delta) {
    var bot = entry.data;
    var mood = bot.mood || "idle";
    var figure = entry.figure;
    var parts = figure.userData;
    var t = time + entry.phase;
    var walking = false;

    if (mood === "planning" || mood === "idle") {
      var distance = figure.position.distanceTo(entry.target);
      if (distance < 0.08) {
        entry.pause -= delta;
        if (entry.pause <= 0) {
          if (mood === "planning") {
            var atBoard = entry.target.distanceTo(entry.seat) > 1;
            entry.target = atBoard ? entry.seat.clone() : entry.outward.clone().multiplyScalar(3.9);
            entry.pause = 2.5;
          } else {
            var angle = entry.angle + (Math.random() - 0.5) * 1.2;
            var radius = 4.2 + Math.random() * 5.6;
            entry.target = new THREE.Vector3(Math.sin(angle) * radius, 0, Math.cos(angle) * radius);
            entry.pause = 2 + Math.random() * 4;
          }
        } else if (mood === "planning" && entry.target.distanceTo(entry.seat) > 1) {
          figure.rotation.y = entry.angle + Math.PI;  // look at the board
        }
      } else {
        walking = true;
        var speed = (mood === "planning" ? 1.5 : 0.8) * (REDUCED ? 0.5 : 1);
        var step = Math.min(distance, speed * delta);
        var direction = entry.target.clone().sub(figure.position).normalize();
        figure.position.addScaledVector(direction, step);
        var heading = Math.atan2(direction.x, direction.z);
        var turn = ((heading - figure.rotation.y + Math.PI * 3) % TAU) - Math.PI;
        figure.rotation.y += turn * Math.min(1, delta * 8);
      }
    } else {
      figure.position.lerp(entry.seat, Math.min(1, delta * 2));
      var face = entry.angle + Math.PI;
      var diff = ((face - figure.rotation.y + Math.PI * 3) % TAU) - Math.PI;
      figure.rotation.y += diff * Math.min(1, delta * 4);
    }

    var bob = 0;
    if (walking) {
      bob = Math.abs(Math.sin(t * 9)) * 0.08;
      parts.arms[0].rotation.x = Math.sin(t * 9) * 0.6;
      parts.arms[1].rotation.x = -Math.sin(t * 9) * 0.6;
    } else if (mood === "working") {
      bob = Math.sin(t * 6) * 0.02;
      parts.arms[0].rotation.x = -1.0 + Math.sin(t * 16) * 0.25;
      parts.arms[1].rotation.x = -1.0 + Math.cos(t * 16) * 0.25;
      parts.head.rotation.x = 0.15;
    } else if (mood === "review") {
      bob = Math.sin(t * 2) * 0.03;
      parts.head.rotation.y = Math.sin(t * 1.6) * 0.5;
      parts.arms[0].rotation.x = 0;
      parts.arms[1].rotation.x = -0.4;
    } else {
      bob = Math.sin(t * 2) * 0.04;
      parts.arms[0].rotation.x *= 0.9;
      parts.arms[1].rotation.x *= 0.9;
      parts.head.rotation.x *= 0.9;
      parts.head.rotation.y *= 0.9;
    }
    if (REDUCED) {
      bob *= 0.3;
    }
    figure.position.y = bob;
    var blink = mood === "queued" ? 0.55 + 0.45 * Math.sin(t * 3) : 1;
    parts.lamp.scale.setScalar(0.8 + 0.4 * blink);

    var screen = entry.desk.userData.screen.material;
    if (mood === "working") {
      screen.emissiveIntensity = 0.75 + Math.sin(t * 13) * 0.12 + Math.sin(t * 31) * 0.06;
    }
    entry.desk.userData.dataBits.forEach(function (bit, index) {
      bit.visible = mood === "working" && !REDUCED;
      if (bit.visible) {
        var cycle = (t * 0.6 + index / 3) % 1;
        bit.position.set((index - 1) * 0.3, 1.85 + cycle * 1.2, -0.15);
        bit.rotation.set(cycle * 4, cycle * 6, 0);
        bit.scale.setScalar(1 - cycle * 0.7);
      }
    });

    var showBubble = this.selected === bot.id || mood === "working" || mood === "review";
    entry.bubble.visible = showBubble;
    if (showBubble) {
      entry.bubble.position.set(figure.position.x, 3.35 + bob, figure.position.z);
    }
    entry.halo.position.set(figure.position.x, 0.03, figure.position.z);
    if (entry.halo.visible) {
      entry.halo.material.opacity = 0.55 + 0.35 * Math.sin(time * 4);
    }
  };

  Arena.prototype.loop = function () {
    requestAnimationFrame(this.loop);
    if (document.hidden) {
      return;
    }
    var delta = Math.min(0.05, this.clock.getDelta());
    var time = this.clock.elapsedTime;
    if (!REDUCED && time - this.lastInteraction > 5) {
      this.view.theta += delta * 0.04;
    }
    var view = this.view;
    this.camera.position.set(
      view.target.x + view.radius * Math.sin(view.phi) * Math.sin(view.theta),
      view.target.y + view.radius * Math.cos(view.phi),
      view.target.z + view.radius * Math.sin(view.phi) * Math.cos(view.theta)
    );
    this.camera.lookAt(view.target);
    var self = this;
    Object.keys(this.bots).forEach(function (key) {
      self.animateBot(self.bots[key], time, delta);
    });
    this.board.rotation.y = REDUCED ? 0 : Math.sin(time * 0.2) * 0.15;
    this.renderer.render(this.scene, this.camera);
    this.frames += 1;
  };

  function webglAvailable() {
    try {
      var canvas = document.createElement("canvas");
      return !!(window.WebGLRenderingContext && (canvas.getContext("webgl2") || canvas.getContext("webgl")));
    } catch (error) {
      return false;
    }
  }

  window.LabArena = {
    supported: function () {
      return !!THREE && webglAvailable();
    },
    create: function (container, state, onSelect) {
      return new Arena(container, state, onSelect);
    },
    moodColor: moodColor,
    segColor: segColor,
    moodText: MOOD_TEXT,
    shorten: shorten
  };
})();
