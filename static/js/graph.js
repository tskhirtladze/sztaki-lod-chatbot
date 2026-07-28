
// ═══════════════════════════════════════════════════════
//  COLOUR / TYPE HELPERS
// ═══════════════════════════════════════════════════════
const TYPE_COLOR = {
    work:    '#f0c040',
    person:  '#4d9fff',
    sound:   '#3dd68c',
    concept: '#b97dff',
    series:  '#f04f5a',
    other:   '#6b7fa0',
};
const TYPE_LABEL = {
    work: 'dbo:Work', person: 'foaf:Person',
    sound: 'dcmitype:Sound', concept: 'skos:Concept',
    series: 'Series', other: 'Resource',
};

function typeColor(t) { return TYPE_COLOR[t] || TYPE_COLOR.other; }


// ═══════════════════════════════════════════════════════
//  PHYSICS GRAPH ENGINE
// ═══════════════════════════════════════════════════════
const canvas  = document.getElementById('graph-canvas');
const ctx     = canvas.getContext('2d');
const tooltip = document.getElementById('node-tooltip');

let nodes = [];
let edges = [];
let scale  = 1;
let offsetX = 0, offsetY = 0;
let dragging = null, dragOffX = 0, dragOffY = 0;
let isPanning = false, panStartX = 0, panStartY = 0, panBaseX = 0, panBaseY = 0;
let hoveredNode  = null;
let selectedNode = null;
let activeFilters = new Set(['work', 'person', 'sound', 'concept', 'series', 'other']);
let searchTerm = '';
let animFrame;
let simRunning = true;
let tickCount  = 0;

async function initGraph() {
    try {
        const resp = await fetch('/graph/data');
        const data = await resp.json();

        const RAW_NODES = data.nodes;
        const RAW_EDGES = data.edges;

        const nodeMap = {};
        RAW_NODES.forEach(n => {
            const node = {
                id: n.id, type: n.type, label: n.label, props: n.props || {},
                x: (Math.random() - .5) * 600,
                y: (Math.random() - .5) * 600,
                vx: 0, vy: 0,
                r: n.type === 'work'   ? 14
                 : n.type === 'person' ? 17
                 : n.type === 'series' ? 16 : 12,
            };
            nodeMap[n.id] = node;
            nodes.push(node);
        });

        edges = RAW_EDGES
            .map(e => ({ ...e, source: nodeMap[e.s], target: nodeMap[e.t] }))
            .filter(e => e.source && e.target);

        document.getElementById('stat-nodes').textContent = nodes.length;
        document.getElementById('stat-edges').textContent = edges.length;

        buildNodeList();
        resize();
        loop();

    } catch (err) {
        console.error('Failed to load graph data:', err);
        document.getElementById('graph-loading').innerHTML =
            `<div style="color:#f04f5a;font-family:monospace;font-size:12px;">
                Failed to load graph data.<br>${err.message}
            </div>`;
        return;
    }

    setTimeout(() => {
        document.getElementById('graph-loading').classList.add('hidden');
    }, 900);
}


// ── Force simulation ──────────────────────────────────
function simulate() {
    if (!simRunning) return;
    const REPEL  = 3200;
    const SPRING = 0.04;
    const IDEAL  = 120;
    const DAMP   = 0.82;
    const GRAV   = 0.003;

    // repulsion
    for (let i = 0; i < nodes.length; i++) {
        for (let j = i + 1; j < nodes.length; j++) {
            const a = nodes[i], b = nodes[j];
            const dx = b.x - a.x, dy = b.y - a.y;
            const d2 = dx*dx + dy*dy + 1;
            const f  = REPEL / d2;
            const d  = Math.sqrt(d2);
            a.vx -= f * dx / d;  a.vy -= f * dy / d;
            b.vx += f * dx / d;  b.vy += f * dy / d;
        }
    }
    // spring
    edges.forEach(e => {
        const dx = e.target.x - e.source.x;
        const dy = e.target.y - e.source.y;
        const d  = Math.sqrt(dx*dx + dy*dy) || 1;
        const f  = (d - IDEAL) * SPRING;
        e.source.vx += f * dx / d;  e.source.vy += f * dy / d;
        e.target.vx -= f * dx / d;  e.target.vy -= f * dy / d;
    });
    // gravity to centre
    nodes.forEach(n => {
        n.vx -= n.x * GRAV;
        n.vy -= n.y * GRAV;
        n.vx *= DAMP; n.vy *= DAMP;
        n.x  += n.vx; n.y  += n.vy;
    });

    tickCount++;
    if (tickCount > 400) simRunning = false;
}

// ── Render ────────────────────────────────────────────
function draw() {
    const W = canvas.width, H = canvas.height;
    ctx.clearRect(0, 0, W, H);
    ctx.save();
    ctx.translate(W/2 + offsetX, H/2 + offsetY);
    ctx.scale(scale, scale);

    // edges
    edges.forEach(e => {
        const visible = isVisible(e.source) && isVisible(e.target);
        ctx.globalAlpha = visible ? .35 : .05;
        const isSel = selectedNode && (e.source === selectedNode || e.target === selectedNode);
        ctx.strokeStyle = isSel ? '#4d9fff' : '#2a3650';
        ctx.lineWidth   = isSel ? 1.5 : .8;
        ctx.setLineDash(isSel ? [] : [3,4]);
        ctx.beginPath();
        ctx.moveTo(e.source.x, e.source.y);
        ctx.lineTo(e.target.x, e.target.y);
        ctx.stroke();
        ctx.setLineDash([]);
        ctx.globalAlpha = 1;

        // relation label on selected edges
        if (isSel && visible) {
            const mx = (e.source.x + e.target.x) / 2;
            const my = (e.source.y + e.target.y) / 2;
            ctx.save();
            ctx.font = `${9/scale}px JetBrains Mono, monospace`;
            ctx.fillStyle = '#6b7fa0';
            ctx.textAlign = 'center';
            ctx.fillText(e.rel, mx, my - 4);
            ctx.restore();
        }
    });

    // nodes
    nodes.forEach(n => {
        const vis = isVisible(n);
        ctx.globalAlpha = vis ? 1 : .12;
        const col   = typeColor(n.type);
        const isHov = n === hoveredNode;
        const isSel = n === selectedNode;
        const r = n.r + (isHov ? 3 : 0) + (isSel ? 2 : 0);

        // glow
        if (isHov || isSel) {
            ctx.shadowColor = col;
            ctx.shadowBlur  = isSel ? 20 : 12;
        }

        // outer ring on selected
        if (isSel) {
            ctx.beginPath();
            ctx.arc(n.x, n.y, r + 4, 0, Math.PI*2);
            ctx.strokeStyle = col;
            ctx.lineWidth   = 1.5;
            ctx.globalAlpha = .4;
            ctx.stroke();
            ctx.globalAlpha = vis ? 1 : .12;
        }

        // fill
        ctx.beginPath();
        ctx.arc(n.x, n.y, r, 0, Math.PI*2);
        const grad = ctx.createRadialGradient(n.x - r*.3, n.y - r*.3, 0, n.x, n.y, r);
        grad.addColorStop(0, col + 'dd');
        grad.addColorStop(1, col + '66');
        ctx.fillStyle = grad;
        ctx.fill();

        // border
        ctx.strokeStyle = col;
        ctx.lineWidth   = isSel ? 2 : 1;
        ctx.stroke();
        ctx.shadowBlur  = 0;

        // label
        const labelScale = Math.max(.7, Math.min(1, scale));
        ctx.font = `${(isHov || isSel ? 600 : 400)} ${11 / scale}px Inter, sans-serif`;
        ctx.fillStyle = isHov || isSel ? '#e8f0ff' : '#8899bb';
        ctx.textAlign = 'center';
        const shortLabel = n.label.length > 22 ? n.label.slice(0, 22) + '…' : n.label;
        ctx.fillText(shortLabel, n.x, n.y + r + 14/scale);

        ctx.globalAlpha = 1;
    });

    ctx.restore();
}

function isVisible(n) {
    if (!activeFilters.has(n.type)) return false;
    if (searchTerm && !n.label.toLowerCase().includes(searchTerm)) return false;
    return true;
}

function loop() {
    simulate();
    draw();
    animFrame = requestAnimationFrame(loop);
}

// ── Resize ────────────────────────────────────────────
function resize() {
    const wrap = document.getElementById('canvas-wrap');
    canvas.width  = wrap.clientWidth;
    canvas.height = wrap.clientHeight;
}
window.addEventListener('resize', resize);

// ── Mouse interactions ────────────────────────────────
function canvasToWorld(cx, cy) {
    return {
        x: (cx - canvas.width/2  - offsetX) / scale,
        y: (cy - canvas.height/2 - offsetY) / scale,
    };
}
function nodeAt(wx, wy) {
    for (let i = nodes.length - 1; i >= 0; i--) {
        const n = nodes[i];
        if (!isVisible(n)) continue;
        const dx = n.x - wx, dy = n.y - wy;
        if (dx*dx + dy*dy < (n.r + 4) * (n.r + 4)) return n;
    }
    return null;
}

canvas.addEventListener('mousemove', e => {
    const rect = canvas.getBoundingClientRect();
    const cx = e.clientX - rect.left, cy = e.clientY - rect.top;

    if (dragging) {
        dragging.x = (cx - canvas.width/2  - offsetX) / scale - dragOffX;
        dragging.y = (cy - canvas.height/2 - offsetY) / scale - dragOffY;
        dragging.vx = 0; dragging.vy = 0;
        simRunning = true; tickCount = 0;
        return;
    }
    if (isPanning) {
        offsetX = panBaseX + (cx - panStartX);
        offsetY = panBaseY + (cy - panStartY);
        return;
    }

    const w = canvasToWorld(cx, cy);
    const n = nodeAt(w.x, w.y);
    hoveredNode = n;
    canvas.style.cursor = n ? 'pointer' : 'grab';

    if (n) {
        showTooltip(n, e.clientX, e.clientY);
    } else {
        tooltip.style.display = 'none';
    }
});

canvas.addEventListener('mousedown', e => {
    const rect = canvas.getBoundingClientRect();
    const cx = e.clientX - rect.left, cy = e.clientY - rect.top;
    const w  = canvasToWorld(cx, cy);
    const n  = nodeAt(w.x, w.y);

    if (n) {
        dragging = n;
        dragOffX = n.x - w.x;
        dragOffY = n.y - w.y;
        n.vx = 0; n.vy = 0;
    } else {
        isPanning  = true;
        panStartX  = cx; panStartY = cy;
        panBaseX   = offsetX; panBaseY = offsetY;
        canvas.style.cursor = 'grabbing';
    }
});

canvas.addEventListener('mouseup', e => {
    if (dragging) {
        const rect = canvas.getBoundingClientRect();
        const cx = e.clientX - rect.left, cy = e.clientY - rect.top;
        const w  = canvasToWorld(cx, cy);
        const n  = nodeAt(w.x, w.y);
        if (n && Math.abs(n.vx) < 1 && Math.abs(n.vy) < 1) {
            selectNode(n);
        }
        dragging = null;
    }
    isPanning = false;
    canvas.style.cursor = 'grab';
});

canvas.addEventListener('click', e => {
    if (isPanning) return;
    const rect = canvas.getBoundingClientRect();
    const w = canvasToWorld(e.clientX - rect.left, e.clientY - rect.top);
    const n = nodeAt(w.x, w.y);
    if (n) selectNode(n);
    else {
        selectedNode = null;
        document.getElementById('detail-panel').classList.add('empty');
        document.getElementById('stat-selected').style.display = 'none';
        document.querySelectorAll('.node-item').forEach(el => el.classList.remove('selected'));
    }
});

canvas.addEventListener('wheel', e => {
    e.preventDefault();
    const factor = e.deltaY < 0 ? 1.1 : 0.9;
    zoomBy(factor);
}, { passive: false });

// ── Tooltip ───────────────────────────────────────────
function showTooltip(n, cx, cy) {
    document.getElementById('tt-title').textContent = n.label;
    document.getElementById('tt-type').textContent  = TYPE_LABEL[n.type] || n.type;
    document.getElementById('tt-type').style.color  = typeColor(n.type);

    const propsEl = document.getElementById('tt-props');
    propsEl.innerHTML = '';
    if (n.props) {
        Object.entries(n.props).slice(0,3).forEach(([k,v]) => {
            const row = document.createElement('div');
            row.className = 'tooltip-row';
            row.innerHTML = `<span class="tooltip-key">${k}</span> ${v}`;
            propsEl.appendChild(row);
        });
    }

    const tw = tooltip.offsetWidth || 220;
    const th = tooltip.offsetHeight || 100;
    const vw = window.innerWidth, vh = window.innerHeight;
    let left = cx + 14, top = cy - 20;
    if (left + tw > vw - 10) left = cx - tw - 14;
    if (top + th > vh - 10) top = vh - th - 10;
    tooltip.style.left    = left + 'px';
    tooltip.style.top     = top  + 'px';
    tooltip.style.display = 'block';
}

// ── Select node ───────────────────────────────────────
function selectNode(n) {
    selectedNode = n;
    simRunning = true; tickCount = 0;

    // detail panel
    document.getElementById('detail-panel').classList.remove('empty');
    document.getElementById('stat-selected').style.display = '';
    document.getElementById('stat-sel-label').textContent = n.label.slice(0, 18) + (n.label.length > 18 ? '…' : '');

    renderDetail(n);

    // highlight list item
    document.querySelectorAll('.node-item').forEach(el => {
        el.classList.toggle('selected', el.dataset.id === n.id);
    });
}

function renderDetail(n) {
    const col = typeColor(n.type);
    let html = `
        <div class="detail-header">
            <div class="detail-node-type" style="color:${col}">${TYPE_LABEL[n.type] || n.type}</div>
            <div class="detail-node-title">${n.label}</div>
        </div>
        <div class="detail-body">`;

    if (n.props && Object.keys(n.props).length) {
        html += `<div class="detail-section-title">Properties</div>`;
        Object.entries(n.props).forEach(([k, v]) => {
            html += `<div class="detail-prop">
                <div class="detail-prop-key">${k}</div>
                <div class="detail-prop-val">${v}</div>
            </div>`;
        });
    }

    // connected edges
    const connected = edges.filter(e => e.source === n || e.target === n);
    if (connected.length) {
        html += `<div class="detail-section-title">Connections (${connected.length})</div>`;
        connected.forEach(e => {
            const other = e.source === n ? e.target : e.source;
            const dir   = e.source === n ? '→' : '←';
            const c2    = typeColor(other.type);
            html += `<div class="conn-item" onclick="selectNodeById('${other.id}')">
                <div class="conn-dot" style="background:${c2}"></div>
                <div class="conn-label">${other.label}</div>
                <div class="conn-rel">${dir} ${e.rel}</div>
            </div>`;
        });
    }

    html += `</div>`;
    document.getElementById('detail-content').innerHTML = html;
}

function selectNodeById(id) {
    const n = nodes.find(n => n.id === id);
    if (n) selectNode(n);
}

// ── Node list (left panel) ────────────────────────────
function buildNodeList() {
    const list = document.getElementById('node-list');
    list.innerHTML = '';
    const visible = nodes.filter(n => isVisible(n));

    if (!visible.length) {
        list.innerHTML = '<div class="list-empty">No nodes match filters</div>';
        return;
    }

    visible.forEach(n => {
        const item = document.createElement('div');
        item.className = 'node-item';
        item.dataset.id = n.id;
        item.innerHTML = `
            <div class="node-dot" style="background:${typeColor(n.type)}"></div>
            <div class="node-label">${n.label}</div>
            <div class="node-type-tag">${n.type}</div>`;
        item.addEventListener('click', () => {
            selectNode(n);
            // pan to node
            offsetX = -n.x * scale;
            offsetY = -n.y * scale;
        });
        list.appendChild(item);
    });
}

function filterNodes() {
    searchTerm = document.getElementById('search-input').value.toLowerCase().trim();
    buildNodeList();
    simRunning = true; tickCount = 0;
}

function toggleFilter(btn) {
    const t = btn.dataset.type;
    if (activeFilters.has(t)) {
        if (activeFilters.size === 1) return; // keep at least one
        activeFilters.delete(t);
        btn.classList.remove('active');
    } else {
        activeFilters.add(t);
        btn.classList.add('active');
    }
    buildNodeList();
    simRunning = true; tickCount = 0;
}

// ── Camera ────────────────────────────────────────────
function zoomBy(factor) {
    scale = Math.max(.2, Math.min(4, scale * factor));
    simRunning = true;
}

function resetLayout() {
    scale = 1; offsetX = 0; offsetY = 0;
    simRunning = true; tickCount = 0;
    // re-scatter
    nodes.forEach(n => {
        n.x = (Math.random() - .5) * 500;
        n.y = (Math.random() - .5) * 500;
        n.vx = 0; n.vy = 0;
    });
}

let expanded = false;
function toggleExpand() {
    expanded = !expanded;
    document.getElementById('btn-expand').querySelector('svg').style.opacity = expanded ? '.5' : '1';
    // zoom out to see everything
    scale = expanded ? .55 : 1;
    offsetX = 0; offsetY = 0;
}

// ── Boot ──────────────────────────────────────────────
window.addEventListener('load', initGraph);
