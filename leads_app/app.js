// Rushton's leads app — the team's live leads CRM.
//
// Reads leads straight from Supabase (signed in with an emailed link; only
// people in public.app_users can sign in or see anything) and saves every
// action through the database functions in rushtons_engine/leads_app.sql,
// which credit it to the signed-in person. Realtime pushes new leads and
// colleagues' changes in as they happen. The lead logic below began as the
// commercial dashboard's leads area (dashboard.py) and keeps its rules.
(function(){
  'use strict';
  var C = window.LEADS_CONFIG;
  var sb = window.supabase.createClient(C.supabaseUrl, C.anonKey, {
    auth: {persistSession: true, autoRefreshToken: true, detectSessionInUrl: true,
      flowType: 'implicit'}});
  var DATA = {leads: [], directory: [], leadTeam: [],
    leadStages: C.stages, leadFollowUp: C.followUpDays, leadQuietDays: C.quietDays,
    leadLost: C.lostReasons, leadVerdicts: C.verdicts, leadVerdictDue: C.verdictDueDays};
  // Not-yet-saved actions, drawn on top of the data until the save lands.
  var STATE = {leadLog: [], leadMatch: {}};
  var ME = '';
  var esc = function(s){ return String(s==null?'':s)
    .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
    .replace(/"/g,'&quot;'); };
  function say(code, msg, ctx){
    var s = document.getElementById('say-' + (ctx || 'r') + '-' + code);
    if(s){ s.textContent = msg; setTimeout(function(){ s.textContent = ''; }, 5000); }
  }
  function dirRow(code){
    var d = DATA.directory.filter(function(x){ return x.c === code; });
    return d.length ? d[0] : null;
  }
  var nameToCode = {};

  document.getElementById('dashlink').href = C.dashboardUrl;

  var STAGES = DATA.leadStages || [];
  var STAGE_LBL = {};
  STAGES.forEach(function(s){ STAGE_LBL[s[0]] = s[1]; });
  var OPEN = ['New', 'Contacted', 'Qualified', 'Trial'];
  var FUNNEL = ['New', 'Contacted', 'Qualified', 'Trial', 'Acquired'];
  var CONTACT_KINDS = {call:'Called', email:'Emailed', whatsapp:'WhatsApp',
    visit:'Visited', sample:'Sample sent'};
  var LKIND = {stage:'Stage', note:'Note', follow_up:'Follow-up', owner:'Owner',
    verdict:'Verdict',
    call:'Called', email:'Emailed', whatsapp:'WhatsApp', visit:'Visited',
    sample:'Sample sent', received:'Enquiry'};
  var VERB = {New:'Respond', Contacted:'Chase', Qualified:'Keep warm',
    Trial:'Get feedback'};
  var curLead = null;
  var openLostFor = null;

  // The signed-in person (app_users.name) gets the credit for every action.
  function whoAmI(){ return ME; }

  function isoDay(d){
    return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0')
      + '-' + String(d.getDate()).padStart(2, '0');
  }
  function todayIso(){ return isoDay(new Date()); }
  function noon(iso){ return new Date(iso.slice(0, 10) + 'T12:00:00'); }
  function addDays(iso, n){
    var d = noon(iso); d.setDate(d.getDate() + n); return isoDay(d);
  }
  function dayDiff(a, b){ return Math.round((noon(b) - noon(a)) / 864e5); }
  function dayOf(x){ return !x ? '' : (x.length > 10 ? isoDay(new Date(x)) : x); }
  function fmtDay(iso){
    if(!iso) return '';
    return noon(iso).toLocaleDateString('en-GB',
      {weekday:'short', day:'numeric', month:'short'});
  }

  function leadBySid(sid){
    var r = (DATA.leads || []).filter(function(l){ return l.sid === sid; });
    return r.length ? r[0] : null;
  }
  function effMatch(l){
    return Object.prototype.hasOwnProperty.call(STATE.leadMatch, l.sid)
      ? (STATE.leadMatch[l.sid] || '') : (l.matched || '');
  }
  function sugFor(l){
    if(effMatch(l)) return null;
    if(Object.prototype.hasOwnProperty.call(STATE.leadMatch, l.sid)) return null;
    return l.sug || null;   // [code, score, how] — user dismissed = key present
  }
  function leadActs(l){
    var a = (l.acts || []).map(function(x){
      return {at:x[0], kind:x[1], stage:x[2], note:x[3], due:x[4], actor:x[5]}; });
    STATE.leadLog.forEach(function(e){
      if(e.sid !== l.sid) return;
      a.push({at:e.at, kind:e.kind, stage:e.stage || '', due:e.due || '',
        actor:e.actor || '', p:1,
        note: e.kind === 'stage' ? (e.reason || '')
          : (e.kind === 'owner' ? (e.owner || '') : (e.note || ''))});
    });
    a.sort(function(x, y){ return x.at < y.at ? -1 : (x.at > y.at ? 1 : 0); });
    return a;
  }
  // Effective pipeline state of a lead, and when it is next due.
  function leadView(l){
    var v = {st: l.status || 'New', owner: l.owner || '', next: l.next || '',
      verdict: l.verdict || '', vsrc: l.vsrc || '',
      lost: l.lost || '', touch: dayOf(l.chg || l.recv || l.date || ''),
      acts: leadActs(l), reached: 0};
    v.acts.forEach(function(e){
      if(e.p){
        if(e.kind === 'stage'){
          v.st = e.stage; v.lost = e.stage === 'Discarded' ? e.note : '';
          v.next = e.due || '';
        } else if(e.kind === 'follow_up') v.next = e.due || '';
        else if(e.kind === 'owner') v.owner = e.note || '';
        else if(e.kind === 'verdict'){ v.verdict = e.note; v.vsrc = 'team'; }
        else if(e.due) v.next = e.due;
      }
      if(e.kind !== 'owner' && e.kind !== 'follow_up'){
        var d = dayOf(e.at);
        if(d > v.touch) v.touch = d;
      }
      if(e.kind === 'stage' && FUNNEL.indexOf(e.stage) > v.reached)
        v.reached = FUNNEL.indexOf(e.stage);
      if(CONTACT_KINDS[e.kind] && v.reached < 1) v.reached = 1;
    });
    if(FUNNEL.indexOf(v.st) > v.reached) v.reached = FUNNEL.indexOf(v.st);
    v.open = OPEN.indexOf(v.st) >= 0;
    v.due = ''; v.auto = false; v.dueIn = null; v.quiet = false;
    // An AI Reject waits for a person to confirm it; it has no follow-up.
    v.rejectCheck = v.open && v.verdict === 'Reject' && !v.next;
    if(v.open && !v.rejectCheck){
      if(v.next) v.due = v.next;
      else if(v.touch){
        var vd = (DATA.leadVerdictDue || {})[v.verdict];
        v.ruleDays = v.st === 'New' && vd !== undefined ? vd
          : ((DATA.leadFollowUp || {})[v.st] || 7);
        v.due = addDays(v.touch, v.ruleDays);
        v.auto = true;
      }
      var t = todayIso();
      v.quiet = !v.next && !!v.touch
        && dayDiff(v.touch, t) > (DATA.leadQuietDays || 60);
      if(v.due) v.dueIn = dayDiff(t, v.due);
    }
    return v;
  }

  var VLABEL = {'Pursue with urgency': 'URGENT', 'Pursue': 'Pursue',
    'Nurture': 'Nurture', 'Pass to rep': 'Pass to rep', 'Reject': 'AI: reject'};
  var VCOLOR = {'Pursue with urgency': '#d03b3b', 'Pursue': '#007946',
    'Nurture': '#5f6b7a', 'Pass to rep': '#c98500', 'Reject': '#898781'};
  function verdictChip(vd){
    if(!vd) return '';
    var c = VCOLOR[vd] || '#898781';
    return '<span class="pill" style="border:1px solid ' + c + ';color:' + c
      + (vd === 'Pursue with urgency' ? ';font-weight:700' : '') + '">'
      + esc(VLABEL[vd] || vd) + '</span>';
  }
  function lostReasonFor(why){
    if(/delivery area/i.test(why)) return 'Outside delivery area';
    if(/consumer|personal/i.test(why)) return 'Personal / not trade';
    return 'Not a fit';
  }
  function rejectBtns(l){
    return '<button class="act mini" data-vlost="' + esc(l.sid) + '">Confirm Lost</button>'
      + '<button class="act mini" data-vpursue="' + esc(l.sid) + '">Pursue anyway</button>';
  }
  function leadChip(st){
    var c = st === 'Acquired' ? '#007946' : st === 'Discarded' ? '#898781'
      : '#c98500';
    return '<span class="pill" style="background:' + c + '1f;color:' + c + '">'
      + esc(STAGE_LBL[st] || st || '—') + '</span>';
  }
  function dueBadge(v){
    if(!v.open) return '';
    if(v.quiet) return '<span class="due">quiet ' + dayDiff(v.touch, todayIso())
      + 'd</span>';
    if(v.dueIn === null) return '';
    if(v.dueIn < 0) return '<span class="due over">overdue ' + (-v.dueIn) + 'd</span>';
    if(v.dueIn === 0) return '<span class="due today">due today</span>';
    return '<span class="due">due ' + esc(fmtDay(v.due)) + '</span>';
  }
  function stars(r){
    if(!r) return '<span class="muted">unrated</span>';
    return '<span class="stars">' + '★'.repeat(r) + '☆'.repeat(5 - r) + '</span>';
  }
  function lname(l){ return l.biz || l.name || l.email || '(no name)'; }
  function plural(n, w){ return n + ' ' + w + (n === 1 ? '' : 's'); }
  function fval(id){ return (document.getElementById(id) || {}).value || ''; }

  function initLeadFilters(){
    var ls = document.getElementById('leadstatus');
    if(ls) ls.innerHTML = '<option value="open">Open (not won or lost)</option>'
      + '<option value="">All stages</option>'
      + STAGES.map(function(s){
          return '<option value="' + esc(s[0]) + '">' + esc(s[1]) + '</option>';
        }).join('');
    var lvd = document.getElementById('leadverdict');
    if(lvd) lvd.innerHTML = '<option value="">Any verdict</option>'
      + (DATA.leadVerdicts || []).map(function(x){
          return '<option>' + esc(x) + '</option>'; }).join('');
    var lo = document.getElementById('leadowner');
    if(lo) lo.innerHTML = '<option value="">Any owner</option>'
      + '<option value="-">Nobody yet</option>'
      + (DATA.leadTeam || []).map(function(n){
          return '<option>' + esc(n) + '</option>'; }).join('');
  }

  function renderLeads(){
    var host = document.getElementById('leadlist');
    if(!host) return;
    var all = DATA.leads || [];
    var views = {};
    all.forEach(function(l){ views[l.sid] = leadView(l); });
    var included = all.filter(function(l){ return !l.excl; });

    var open = 0, dueNow = 0, won = 0, resp = [];
    included.forEach(function(l){
      var v = views[l.sid];
      if(v.open){
        open++;
        if(!v.quiet && v.dueIn !== null && v.dueIn <= 0) dueNow++;
      }
      if(v.st === 'Acquired') won++;
      if(l.recv){
        var first = null;
        v.acts.forEach(function(e){
          if(!first && (CONTACT_KINDS[e.kind]
              || (e.kind === 'stage' && e.stage !== 'New'))) first = e.at;
        });
        if(first) resp.push((new Date(first) - new Date(l.recv)) / 36e5);
      }
    });
    var matched = included.filter(function(l){ return effMatch(l); }).length;
    var sugs = included.filter(sugFor).length;
    resp.sort(function(a, b){ return a - b; });
    var med = resp.length ? resp[Math.floor(resp.length / 2)] : null;
    var kp = document.getElementById('lead-kpis');
    if(kp) kp.innerHTML =
      '<button class="kpi" data-funnel="open"><span class="n">' + open
      + '</span><span class="l">open leads (of ' + included.length
      + ' commercial)</span></button>'
      + '<button class="kpi" data-tab="leadrecs"><span class="n">' + dueNow
      + '</span><span class="l">follow-ups due now</span></button>'
      + '<button class="kpi" data-funnel="Acquired"><span class="n">' + won
      + '</span><span class="l">won · ' + matched + ' linked to a Fresho account ('
      + (included.length ? Math.round(100 * matched / included.length) : 0)
      + '% real conversion)' + (sugs ? ' · ' + sugs + ' possible match'
      + (sugs > 1 ? 'es' : '') + ' to confirm' : '') + '</span></button>'
      + '<div class="kpi" style="cursor:default"><span class="n">'
      + (med === null ? '—' : (med < 48 ? Math.round(med) + 'h'
         : Math.round(med / 24) + 'd'))
      + '</span><span class="l">typical time to first contact (website leads'
      + (resp.length ? ', ' + resp.length + ' so far' : ', none logged yet')
      + ')</span></div>';

    var fh = document.getElementById('lead-funnel');
    if(fh){
      var n = included.length || 1;
      var h = '<div class="funnel">';
      FUNNEL.forEach(function(st, i){
        var reached = included.filter(function(l){
          return views[l.sid].reached >= i; }).length;
        var now = included.filter(function(l){
          return views[l.sid].st === st; }).length;
        h += '<button class="frow" data-funnel="' + esc(st) + '" title="'
          + now + ' in this stage now">'
          + '<span class="flab">' + esc(STAGE_LBL[st]) + '</span>'
          + '<span class="ftrack"><span class="fbar" style="width:'
          + (100 * reached / n).toFixed(1) + '%"></span></span>'
          + '<span class="fnum"><b>' + reached + '</b> · '
          + Math.round(100 * reached / n) + '%</span></button>';
      });
      var lost = included.filter(function(l){ return views[l.sid].st === 'Discarded'; });
      var why = {};
      lost.forEach(function(l){
        var r = views[l.sid].lost || 'no reason recorded';
        why[r] = (why[r] || 0) + 1; });
      var whyTxt = Object.keys(why).sort(function(a, b){ return why[b] - why[a]; })
        .map(function(k){ return esc(k) + ' ' + why[k]; }).join(' · ');
      h += '</div><div class="flost"><button class="bk" data-funnel="Discarded">Lost: '
        + lost.length + '</button>' + (whyTxt ? ' — ' + whyTxt : '')
        + '. Bars count leads that got at least that far; tap a stage to list'
        + ' the leads in it now.</div>';
      fh.innerHTML = h;
    }

    var rt = document.getElementById('lead-ratings');
    if(rt){
      var rows = '';
      [5, 4, 3, 2, 1, null].forEach(function(band){
        var pool = included.filter(function(l){
          return band === null ? !l.rating : l.rating === band; });
        if(!pool.length) return;
        var a = pool.filter(function(l){
          return views[l.sid].st === 'Acquired'; }).length;
        rows += '<tr><td>' + (band === null ? 'Unrated'
          : '★'.repeat(band)) + '</td><td class="num">' + pool.length
          + '</td><td class="num">' + a + '</td><td class="num">'
          + Math.round(100 * a / pool.length) + '%</td></tr>';
      });
      rt.innerHTML = '<table><tr><th>Rating</th><th>Leads</th>'
        + '<th>Won</th><th>Conversion</th></tr>' + rows + '</table>';
    }

    var sf = fval('leadstatus'), of = fval('leadowner');
    var rf = parseInt(fval('leadrating'), 10) || 0;
    var vf = fval('leadverdict');
    var q = fval('leadq').trim().toLowerCase();
    var showEx = !!(document.getElementById('leadexcl') || {}).checked;
    var list = all.filter(function(l){
      var v = views[l.sid];
      if(!showEx && l.excl) return false;
      if(sf === 'open' && !v.open) return false;
      if(sf && sf !== 'open' && v.st !== sf) return false;
      if(of === '-' && v.owner) return false;
      if(of && of !== '-' && v.owner !== of) return false;
      if(rf && (l.rating || 0) < rf) return false;
      if(vf && v.verdict !== vf) return false;
      if(q && [l.biz, l.name, l.email, l.phone].join(' ').toLowerCase()
          .indexOf(q) < 0) return false;
      return true;
    });
    if(sf === 'open') list.sort(function(a, b){
      var x = views[a.sid], y = views[b.sid];
      if(x.quiet !== y.quiet) return x.quiet ? 1 : -1;
      return (x.dueIn === null ? 1e9 : x.dueIn) - (y.dueIn === null ? 1e9 : y.dueIn);
    });
    host.innerHTML = list.map(function(l){
      var v = views[l.sid];
      return '<div class="lrow' + (l.excl ? ' excl' : '') + '">'
        + '<button class="lhead" data-golead="' + esc(l.sid) + '">'
        + '<span class="rname">' + esc(lname(l)) + '</span>'
        + stars(l.rating) + verdictChip(v.verdict) + leadChip(v.st) + dueBadge(v)
        + (v.owner ? '<span class="owner">' + esc(v.owner) + '</span>' : '')
        + (l.excl ? '<span class="pill">' + esc(l.excl) + '</span>' : '')
        + '<span class="dmeta">' + esc(l.src) + (l.date ? ' · ' + esc(l.date) : '')
        + '</span></button></div>';
    }).join('') || '<p class="said">No leads match this filter.</p>';
  }

  function waNumber(p){
    var d = String(p || '').replace(/\D/g, '');
    if(!d) return '';
    if(d.indexOf('44') === 0) return d;
    if(d.charAt(0) === '0') return '44' + d.slice(1);
    if(d.length === 10 && d.charAt(0) === '7') return '44' + d;
    return d;
  }

  function actLine(e){
    var what = LKIND[e.kind] || e.kind, detail = '';
    if(e.kind === 'stage') detail = '→ ' + (STAGE_LBL[e.stage] || e.stage)
      + (e.stage === 'Discarded' && e.note ? ' (' + e.note + ')' : '');
    else if(e.kind === 'owner') detail = '→ ' + (e.note || 'nobody');
    else if(e.kind === 'verdict') detail = '→ ' + (e.note || '');
    else if(e.kind === 'follow_up') detail = e.due ? 'set for ' + fmtDay(e.due)
      : 'back to the stage rule';
    else {
      detail = e.note || '';
      if(e.due) detail += (detail ? ' · ' : '') + 'follow up ' + fmtDay(e.due);
    }
    return '<div class="tlrow"><span class="tld">' + esc(dayOf(e.at))
      + '</span><span class="tlk">' + esc(what) + '</span><span class="tls">'
      + esc(detail) + (e.actor ? ' · ' + esc(e.actor) : '')
      + (e.p ? ' <span class="pend">(saving…)</span>' : '')
      + '</span></div>';
  }

  function freshoBlock(l){
    var m = effMatch(l), md = m ? dirRow(m) : null, e = esc(l.sid);
    if(m){
      return '<div class="facts"><span>linked to <b>' + esc(md ? md.n : m) + '</b>'
        + (l.method && !STATE.leadMatch.hasOwnProperty(l.sid)
           ? ' <span class="muted">(' + esc(l.method) + ')</span>' : '') + '</span>'
        + (md && md.s ? '<span>account ' + esc(String(md.s).replace(/_/g, ' '))
           + '</span>' : '')
        + (md && md.last ? '<span>last order <b>' + esc(md.last) + '</b></span>' : '')
        + '</div><p class="said">Its orders and &#163; sales are on the <a href="'
        + esc(C.dashboardUrl) + '" target="_blank" rel="noopener">commercial'
        + ' dashboard</a>.</p><div class="acts"><button class="act mini"'
        + ' data-leadclear="' + e + '">Clear link</button>'
        + '<span class="said" id="say-l-' + e + '"></span></div>';
    }
    return '<div class="matchform">'
      + '<input class="act" list="acctnames" id="lm-' + e
      + '" placeholder="Type the Fresho account name…">'
      + '<button class="act" data-leadmatch="' + e
      + '">Link account</button><span class="said" id="say-l-' + e
      + '"></span></div>';
  }

  function renderLeadPage(sid){
    var host = document.getElementById('lead-page');
    var l = leadBySid(sid);
    if(!host || !l) return;
    var v = leadView(l), e = esc(sid);
    var h = '<button class="bk" data-tab="leads">&#8592; All leads</button>';
    h += '<div class="ahead"><h2>' + esc(lname(l)) + '</h2>' + stars(l.rating)
      + leadChip(v.st) + dueBadge(v)
      + (l.excl ? '<span class="pill">' + esc(l.excl) + '</span>' : '') + '</div>';
    h += '<div class="ar">'
      + (l.type ? '<span class="chip">' + esc(l.type) + '</span>' : '')
      + (l.src ? '<span class="chip">' + esc(l.src) + '</span>' : '')
      + (l.heard ? '<span class="chip">heard via ' + esc(l.heard) + '</span>' : '')
      + '<span class="chip">enquired ' + esc(fmtDay(dayOf(l.recv || l.date)))
      + '</span></div>';

    if(v.verdict){
      h += '<div class="lbox"><div class="dh">AI VERDICT'
        + (v.vsrc === 'team' ? ' (changed by the team)' : '') + '</div>'
        + '<div>' + verdictChip(v.verdict) + ' <span class="said">'
        + (v.vsrc === 'team' ? 'Set by the team.' + (l.vwhy ? ' The AI’s note: '
           + esc(l.vwhy) : '') : esc(l.vwhy)) + '</span></div>'
        + (l.urg ? '<p class="said" style="margin:.3rem 0 0">Urgency: '
           + esc(l.urg) + '</p>' : '')
        + '<div class="acts">'
        + (v.rejectCheck ? rejectBtns(l)
           : '<label class="said">Change verdict <select class="act" data-lverdict="'
             + e + '">' + (DATA.leadVerdicts || []).map(function(x){
               return '<option' + (x === v.verdict ? ' selected' : '') + '>'
                 + esc(x) + '</option>'; }).join('') + '</select></label>')
        + '</div></div>';
    }
    h += '<div class="dh">STAGE</div><div class="stepper" role="group" '
      + 'aria-label="Lead stage">';
    STAGES.forEach(function(s){
      var i = FUNNEL.indexOf(s[0]);
      var cls = s[0] === 'Discarded' ? 'lost'
        : (i >= 0 && i < v.reached ? 'done' : '');
      h += '<button class="' + cls + '" data-lstage="' + esc(s[0]) + '" data-sid="'
        + e + '" aria-current="' + (v.st === s[0] ? 'true' : 'false') + '">'
        + esc(s[1]) + '</button>';
    });
    h += '</div>';
    if(v.st === 'Discarded') h += '<p class="said">Lost'
      + (v.lost ? ' — ' + esc(v.lost) : '') + '</p>';
    if(openLostFor === sid){
      h += '<div class="acts"><select class="act" id="lr-' + e
        + '" aria-label="Why was it lost">' + (DATA.leadLost || []).map(function(r){
          return '<option>' + esc(r) + '</option>'; }).join('')
        + '</select><button class="act" data-lostconfirm="' + e
        + '">Mark lost</button><button class="act mini" data-lostcancel="' + e
        + '">Cancel</button></div>';
    }
    if(v.st === 'Acquired' && !effMatch(l)) h += '<p class="said">Won — link the'
      + ' Fresho account below so its orders and sales count for this lead.</p>';
    h += '<div><span class="said" id="say-lp-' + e + '"></span></div>';

    h += '<div class="lpgrid"><div class="lbox"><div class="dh">NEXT FOLLOW-UP</div>';
    if(v.open){
      h += '<div>' + (v.due ? '<b>' + esc(fmtDay(v.due)) + '</b> '
          + (v.dueIn > 0 ? '<span class="said">in ' + plural(v.dueIn, 'day')
             + '</span>' : dueBadge(v)) : '—') + '</div>'
        + '<p class="said" style="margin:.2rem 0 0">' + (v.auto
          ? 'Automatic — ' + plural(v.ruleDays, 'day')
            + ' after the last touch (' + esc(fmtDay(v.touch)) + ') while '
            + esc((STAGE_LBL[v.st] || v.st).toLowerCase()) + '.'
          : 'Set by hand.') + '</p>'
        + '<div class="acts"><input class="act" type="date" data-lfudate="' + e
        + '" value="' + esc(v.next) + '" aria-label="Follow-up date">'
        + '<button class="act mini" data-lfu="1" data-sid="' + e + '">Tomorrow</button>'
        + '<button class="act mini" data-lfu="3" data-sid="' + e + '">In 3 days</button>'
        + '<button class="act mini" data-lfu="7" data-sid="' + e + '">Next week</button>'
        + (v.next ? '<button class="act mini" data-lfu="clear" data-sid="' + e
           + '">Use the rule</button>' : '') + '</div>';
    } else {
      h += '<p class="said">None — this lead is '
        + esc((STAGE_LBL[v.st] || v.st).toLowerCase()) + '.</p>';
    }
    h += '</div><div class="lbox"><div class="dh">OWNER</div>'
      + '<select class="act" data-lowner="' + e + '" aria-label="Lead owner">'
      + '<option value="">Nobody yet</option>'
      + (DATA.leadTeam || []).map(function(n){
          return '<option' + (v.owner === n ? ' selected' : '') + '>' + esc(n)
            + '</option>'; }).join('')
      + '</select><p class="said" style="margin:.5rem 0 0">Your actions are'
      + ' saved as <b>' + esc(ME) + '</b>.</p></div></div>';

    h += '<div class="lbox"><div class="dh">LOG WHAT YOU DID</div>'
      + '<input class="act noteinput" style="width:100%" id="ln-' + e
      + '" maxlength="500" placeholder="What happened? (optional for calls and emails)">'
      + '<div class="acts">'
      + Object.keys(CONTACT_KINDS).map(function(k){
          return '<button class="act" data-lact="' + k + '" data-sid="' + e + '">'
            + esc(CONTACT_KINDS[k]) + '</button>'; }).join('')
      + '<button class="act" data-lnote="' + e + '">Add note</button></div></div>';

    var wa = waNumber(l.phone);
    h += '<div class="dh">CONTACT</div><div class="facts">'
      + (l.name ? '<span><b>' + esc(l.name) + '</b></span>' : '')
      + (l.email ? '<span><a href="mailto:' + esc(l.email) + '?subject='
         + encodeURIComponent('Your enquiry with Rushton’s') + '">'
         + esc(l.email) + '</a></span>' : '')
      + (l.phone ? '<span><a href="tel:' + esc(l.phone.replace(/[^\d+]/g, ''))
         + '">' + esc(l.phone) + '</a></span>' : '')
      + (wa ? '<span><a href="https://wa.me/' + wa
         + '" target="_blank" rel="noopener">WhatsApp</a></span>' : '')
      + '</div>';

    var acts = v.acts.slice();
    acts.push({at: l.recv || l.date, kind: 'received',
      note: 'via ' + (l.src || 'unknown') + (l.web ? ' (website form)' : '')});
    acts.sort(function(x, y){ return x.at < y.at ? 1 : (x.at > y.at ? -1 : 0); });
    h += '<div class="dh">TIMELINE</div><div class="tl">'
      + acts.map(actLine).join('') + '</div>';
    if(!v.acts.length && l.status && l.status !== 'New' && !l.web)
      h += '<p class="said">Stage ' + esc(STAGE_LBL[l.status] || l.status)
        + ' came from the old leads sheet.</p>';

    if(l.pc || l.site) h += '<div class="facts">'
      + (l.pc ? '<span>postcode <b>' + esc(l.pc) + '</b></span>' : '')
      + (l.site ? '<span><a href="' + esc(l.site) + '" target="_blank" rel="noopener">'
         + esc(l.site.replace(/^https?:\/\//, '')) + '</a></span>' : '') + '</div>';
    if(l.srcs && l.srcs.length) h += '<details class="help"><summary>Sources the AI'
      + ' used (' + l.srcs.length + ')</summary><ol class="said">'
      + l.srcs.map(function(u){ return '<li><a href="' + esc(u)
        + '" target="_blank" rel="noopener">' + esc(u) + '</a></li>'; }).join('')
      + '</ol></details>';
    if(l.analysis) h += '<div class="dh">AI RESEARCH</div><div class="draft">'
      + esc(l.analysis) + '</div>';
    if(l.notes || l.details) h += '<div class="dh">NOTES FROM THE OLD SHEET</div>'
      + '<p class="said">' + esc([l.notes, l.details].filter(Boolean).join(' — '))
      + '</p>';
    h += '<div class="dh">FRESHO ACCOUNT</div>' + freshoBlock(l);
    host.innerHTML = h;
  }

  // Saves run one at a time, in order (logging a call can also move the
  // stage). The action shows at once as "saving…" and becomes data on reload.
  var saving = Promise.resolve(), pendSeq = 0;
  function logLead(sid, kind, extra){
    extra = extra || {};
    var ev = {id: 'p' + (++pendSeq), sid: sid, kind: kind,
      at: new Date().toISOString(), actor: ME};
    for(var k in extra) ev[k] = extra[k];
    STATE.leadLog.push(ev);
    rerenderLeads();
    var note = kind === 'stage' ? (extra.reason || '')
      : (kind === 'owner' ? (extra.owner || '') : (extra.note || ''));
    saving = saving.then(function(){
      return sb.rpc('log_lead_activity', {p_sid: sid, p_kind: kind,
        p_stage: extra.stage || null, p_note: note || null, p_due: extra.due || null});
    }).then(function(res){
      if(res.error) throw res.error;
      ev.doneAt = Date.now();
    }).catch(function(err){
      STATE.leadLog = STATE.leadLog.filter(function(x){ return x !== ev; });
      rerenderLeads();
      say(sid, 'Not saved (' + (err && err.message || 'connection problem')
        + ') — please try again', 'lp');
    }).then(scheduleReload);
    return true;
  }
  function linkLead(sid, code){
    STATE.leadMatch[sid] = code;
    rerenderLeads();
    sb.rpc('link_lead_account', {p_sid: sid, p_code: code || null}).then(function(res){
      if(res.error) throw res.error;
      return reload();
    }).catch(function(err){
      say(sid, 'Not saved (' + (err && err.message || 'connection problem')
        + ') — please try again', 'l');
    }).then(function(){ delete STATE.leadMatch[sid]; rerenderLeads(); });
  }

  function rerenderLeads(){
    if(curLead) renderLeadPage(curLead);
    renderLeads();
    renderLeadRecs();
  }


  function renderLeadRecs(){
    var host = document.getElementById('lrlist');
    var me = whoAmI();
    var mine = !!(document.getElementById('fumine') || {}).checked;
    var g = {over: [], today: [], soon: [], quiet: []}, checks = [], dueAll = 0;
    var rejects = [];
    (DATA.leads || []).forEach(function(l){
      if(l.excl) return;
      var v = leadView(l);
      if(v.open && !v.quiet && v.dueIn !== null && v.dueIn <= 0) dueAll++;
      if(v.rejectCheck) dueAll++;
      var sg = sugFor(l);
      if(sg){
        var sd = dirRow(sg[0]);
        if(sd) checks.push({l: l, v: v, verb: 'Confirm', why: 'looks like existing'
          + ' customer ' + sd.n + ' (' + Math.round(sg[1] * 100) + '% ' + sg[2]
          + ') — confirm it on the lead page'});
      } else if(v.st === 'Acquired' && !effMatch(l)){
        checks.push({l: l, v: v, verb: 'Link',
          why: 'won but not linked to a Fresho account yet'});
      }
      if(!v.open) return;
      if(mine && v.owner && v.owner !== me) return;
      var it = {l: l, v: v};
      if(v.rejectCheck){ rejects.push(it); return; }
      if(v.quiet) g.quiet.push(it);
      else if(v.dueIn === null || v.dueIn > 7) return;
      else if(v.dueIn < 0) g.over.push(it);
      else if(v.dueIn === 0) g.today.push(it);
      else g.soon.push(it);
    });
    var badge = document.getElementById('fu-count');
    if(badge) badge.textContent = dueAll ? String(dueAll) : '';
    var hdot = document.getElementById('hdot');
    if(hdot) hdot.hidden = !dueAll;
    if(!host) return;
    var byDue = function(a, b){
      return (a.v.dueIn - b.v.dueIn) || ((b.l.rating || 0) - (a.l.rating || 0)); };
    var card = function(it, verb, why){
      var l = it.l, v = it.v;
      return '<div class="card"><div class="crow">'
        + '<span class="cverb">' + esc(verb || VERB[v.st] || 'Follow up') + '</span>'
        + '<button class="cname" data-golead="' + esc(l.sid) + '">'
        + esc(lname(l)) + '</button>' + stars(l.rating) + verdictChip(v.verdict)
        + dueBadge(v) + '</div>'
        + '<div class="cwhy">' + esc(why || ((STAGE_LBL[v.st] || v.st)
          + ', last touched ' + fmtDay(v.touch)
          + (v.auto ? ' · ' + ((DATA.leadFollowUp || {})[v.st] || 7) + '-day rule'
             : ' · date set by hand')
          + ' · ' + (v.owner || 'nobody owns it yet')
          + (l.src ? ' · ' + l.src : ''))) + '</div></div>';
    };
    var block = function(title, items){
      if(!items.length) return '';
      return '<h3>' + title + ' (' + items.length + ')</h3>'
        + items.sort(byDue).map(function(it){ return card(it); }).join('');
    };
    var h = '';
    if(rejects.length) h += '<h3>AI rejected — check (' + rejects.length + ')</h3>'
      + rejects.map(function(it){
          return '<div class="card"><div class="crow"><span class="cverb">Check</span>'
            + '<button class="cname" data-golead="' + esc(it.l.sid) + '">'
            + esc(lname(it.l)) + '</button>' + verdictChip('Reject')
            + '<span class="cbtns">' + rejectBtns(it.l) + '</span></div>'
            + '<div class="cwhy">' + esc(it.l.vwhy || 'rejected by the AI')
            + '</div></div>'; }).join('');
    h += block('Overdue', g.over) + block('Due today', g.today)
      + block('Next 7 days', g.soon);
    if(!h) h = '<p class="said">Nothing due in the next week'
      + (mine ? ' for you' : '') + '.</p>';
    if(checks.length) h += '<h3>To check (' + checks.length + ')</h3>'
      + checks.map(function(c){ return card(c, c.verb, c.why); }).join('');
    if(g.quiet.length) h += '<details class="help"><summary>Gone quiet — open but'
      + ' untouched for ' + (DATA.leadQuietDays || 60) + '+ days (' + g.quiet.length
      + '): revive or mark Lost</summary>'
      + g.quiet.sort(function(a, b){ return a.v.touch < b.v.touch ? 1 : -1; })
        .map(function(it){ return card(it, 'Revive or close'); }).join('')
      + '</details>';
    host.innerHTML = h;
  }


  // ---------------------------------------------------------------- data
  var LEAD_COLS = 'source_id,name,business,email,phone,lead_source,analysis,'
    + 'notes,submitted_at,status,rating,type,details,excluded_reason,'
    + 'matched_customer_code,match_method,intake,heard_about,page_url,'
    + 'received_at,status_changed_at,owner,next_follow_up,lost_reason,verdict,'
    + 'verdict_reason,verdict_source,urgency,postcode,website,research';
  var ACT_COLS = 'source_id,occurred_at,kind,stage,note,follow_up_on,actor';

  // PostgREST returns at most 1000 rows a call, so read in pages.
  function fetchAll(build){
    var out = [];
    function page(from){
      return build().range(from, from + 999).then(function(res){
        if(res.error) throw res.error;
        out = out.concat(res.data);
        return res.data.length === 1000 ? page(from + 1000) : out;
      });
    }
    return page(0);
  }
  function researchSources(research){
    try {
      var s = JSON.parse(research || '{}').sources || [];
      return s.filter(function(u){
        return typeof u === 'string' && u.indexOf('http') === 0; }).slice(0, 10);
    } catch(err){ return []; }
  }
  // Same shape as dashboard._load_leads, so the lead logic above is unchanged.
  function toLead(r, acts){
    return {sid: r.source_id, name: r.name || '', biz: r.business || '',
      email: r.email || '', phone: r.phone || '', src: r.lead_source || '',
      date: r.submitted_at || '', status: r.status || '', rating: r.rating,
      type: r.type || '', excl: r.excluded_reason || '',
      matched: r.matched_customer_code || '', method: r.match_method || '',
      analysis: (r.analysis || '').trim(), notes: (r.notes || '').slice(0, 500),
      details: (r.details || '').slice(0, 500), web: r.intake === 'web',
      heard: r.heard_about || '', page: r.page_url || '',
      recv: r.received_at || '', chg: r.status_changed_at || '',
      owner: r.owner || '', next: r.next_follow_up || '', lost: r.lost_reason || '',
      acts: acts[r.source_id] || [], verdict: r.verdict || '',
      vwhy: r.verdict_reason || '', vsrc: r.verdict_source || '',
      urg: r.urgency || '', pc: r.postcode || '', site: r.website || '',
      srcs: researchSources(r.research)};
  }
  var loadedAt = null;
  function reload(){
    var t0 = Date.now();
    return Promise.all([
      fetchAll(function(){ return sb.from('leads').select(LEAD_COLS)
        .order('submitted_at', {ascending: false, nullsFirst: false})
        .order('received_at', {ascending: false, nullsFirst: false})
        .order('source_id'); }),
      fetchAll(function(){ return sb.from('lead_activities').select(ACT_COLS)
        .order('occurred_at').order('id'); })
    ]).then(function(r){
      var acts = {};
      r[1].forEach(function(a){
        (acts[a.source_id] = acts[a.source_id] || []).push([a.occurred_at || '',
          a.kind, a.stage || '', a.note || '', a.follow_up_on || '', a.actor || '']);
      });
      DATA.leads = r[0].map(function(x){ return toLead(x, acts); });
      // saved actions are in the data now; keep only those still saving
      STATE.leadLog = STATE.leadLog.filter(function(e){
        return !e.doneAt || e.doneAt > t0; });
      loadedAt = new Date();
      var st = document.getElementById('livestamp');
      if(st) st.textContent = 'updated ' + loadedAt.toLocaleTimeString('en-GB',
        {hour: '2-digit', minute: '2-digit'});
      rerenderLeads();
      if(pendingRoute){ var pr = pendingRoute; pendingRoute = null; go(pr); }
    }).catch(function(err){
      var st = document.getElementById('livestamp');
      if(st) st.textContent = 'could not refresh (' + (err.message || 'offline')
        + ') — retrying';
      setTimeout(scheduleReload, 15000);
    });
  }
  var reloadTimer = null;
  function scheduleReload(){
    if(reloadTimer) clearTimeout(reloadTimer);
    reloadTimer = setTimeout(function(){ reloadTimer = null; reload(); }, 400);
  }

  // ---------------------------------------------------------------- pages
  var TABS = ['leadrecs', 'leads'];
  var hamb = document.getElementById('hamb');
  var sitenav = document.getElementById('sitenav');
  function openNav(){
    sitenav.classList.add('open');
    document.body.classList.add('navopen');
    hamb.setAttribute('aria-expanded', 'true');
    var cur = sitenav.querySelector('[aria-current="true"]')
      || sitenav.querySelector('button[data-tab]');
    if(cur) cur.focus();
  }
  function closeNav(){
    if(!sitenav.classList.contains('open')) return;
    sitenav.classList.remove('open');
    document.body.classList.remove('navopen');
    hamb.setAttribute('aria-expanded', 'false');
  }
  function showSection(id, tab){
    ['sec-leadrecs', 'sec-leads', 'sec-lead'].forEach(function(s){
      document.getElementById(s).classList.toggle('on', s === id); });
    document.querySelectorAll('.nav button[data-tab]').forEach(function(b){
      b.setAttribute('aria-current', b.getAttribute('data-tab') === tab
        ? 'true' : 'false'); });
    var b = document.querySelector('.nav button[data-tab="' + tab + '"]');
    document.getElementById('mcur').textContent = b ? b.firstChild.textContent : '';
    closeNav();
  }
  function showTab(name){
    if(TABS.indexOf(name) < 0) name = 'leadrecs';
    curLead = null; openLostFor = null;
    showSection('sec-' + name, name);
    if(location.hash !== '#' + name){
      try { history.replaceState(null, '', '#' + name); } catch(err){}
    }
  }
  function showLead(sid){
    if(!leadBySid(sid)){
      showTab('leads');
      var nf = document.getElementById('lead-missing');
      nf.textContent = 'That lead was not found — it may have been removed.';
      nf.hidden = false;
      return;
    }
    curLead = sid; openLostFor = null;
    showSection('sec-lead', 'leads');
    renderLeadPage(sid);
    window.scrollTo(0, 0);
  }
  // #lead/<id> inside the app; #lead-<id> from the lead emails ("w|" ids).
  function go(h){
    document.getElementById('lead-missing').hidden = true;
    if(h.indexOf('lead/') === 0) return showLead(decodeURIComponent(h.slice(5)));
    if(h.indexOf('lead-') === 0){
      var id = decodeURIComponent(h.slice(5));
      return showLead(leadBySid('w|' + id) ? 'w|' + id : id);
    }
    showTab(h);
  }
  var pendingRoute = null;
  function route(){
    var h = location.hash.slice(1);
    if(/access_token|error_description/.test(h)) return;
    if(!loadedAt && /^lead[\/-]/.test(h)){ pendingRoute = h; return; }
    go(h);
  }

  // ---------------------------------------------------------------- events
  document.addEventListener('click', function(ev){
    var t = ev.target.closest ? ev.target : ev.target.parentNode;
    if(t.closest('#hamb')){ openNav(); return; }
    if(t.closest('[data-navclose]')){ closeNav(); return; }
    var tb = t.closest('[data-tab]');
    if(tb){ location.hash = tb.getAttribute('data-tab'); return; }
    var gl = t.closest('[data-golead]');
    if(gl){ location.hash = 'lead/' + encodeURIComponent(gl.getAttribute('data-golead')); return; }
    var fr = t.closest('[data-funnel]');
    if(fr){
      document.getElementById('leadstatus').value = fr.getAttribute('data-funnel');
      if(!document.getElementById('sec-leads').classList.contains('on')) showTab('leads');
      renderLeads();
      document.getElementById('leadlist').scrollIntoView({block: 'start'});
      return;
    }
    var lsg = t.closest('[data-lstage]');
    if(lsg){
      var gsid = lsg.getAttribute('data-sid'), gst = lsg.getAttribute('data-lstage');
      if(gst === leadView(leadBySid(gsid)).st) return;
      if(gst === 'Discarded'){ openLostFor = gsid; renderLeadPage(gsid); return; }
      openLostFor = null;
      logLead(gsid, 'stage', {stage: gst});
      return;
    }
    var lok = t.closest('[data-lostconfirm]');
    if(lok){
      var osid = lok.getAttribute('data-lostconfirm');
      var rsel = document.getElementById('lr-' + osid);
      openLostFor = null;
      logLead(osid, 'stage', {stage: 'Discarded', reason: rsel ? rsel.value : ''});
      return;
    }
    var lcn = t.closest('[data-lostcancel]');
    if(lcn){ openLostFor = null; renderLeadPage(lcn.getAttribute('data-lostcancel')); return; }
    var lfu = t.closest('[data-lfu]');
    if(lfu){
      var k = lfu.getAttribute('data-lfu');
      logLead(lfu.getAttribute('data-sid'), 'follow_up',
        {due: k === 'clear' ? '' : addDays(todayIso(), parseInt(k, 10))});
      return;
    }
    var lac = t.closest('[data-lact]');
    if(lac){
      var asid = lac.getAttribute('data-sid'), akind = lac.getAttribute('data-lact');
      var ain = document.getElementById('ln-' + asid);
      var before = leadView(leadBySid(asid)).st;
      logLead(asid, akind, {note: ain ? ain.value.trim() : ''});
      // doing something with a lead moves it along the funnel
      if(akind === 'sample' && OPEN.indexOf(before) >= 0 && before !== 'Trial')
        logLead(asid, 'stage', {stage: 'Trial'});
      else if(before === 'New') logLead(asid, 'stage', {stage: 'Contacted'});
      return;
    }
    var vlo = t.closest('[data-vlost]');
    if(vlo){
      var vsid = vlo.getAttribute('data-vlost');
      logLead(vsid, 'stage', {stage: 'Discarded',
        reason: lostReasonFor((leadBySid(vsid) || {}).vwhy || '')});
      return;
    }
    var vpu = t.closest('[data-vpursue]');
    if(vpu){ logLead(vpu.getAttribute('data-vpursue'), 'verdict', {note: 'Pursue'}); return; }
    var lnt = t.closest('[data-lnote]');
    if(lnt){
      var nsid = lnt.getAttribute('data-lnote');
      var nin = document.getElementById('ln-' + nsid);
      var txt = nin ? nin.value.trim() : '';
      if(!txt){ say(nsid, 'Type the note first', 'lp'); return; }
      logLead(nsid, 'note', {note: txt});
      return;
    }
    var lm = t.closest('[data-leadmatch]');
    if(lm){
      var msid = lm.getAttribute('data-leadmatch');
      var inp = document.getElementById('lm-' + msid);
      var typed = inp && inp.value ? inp.value.trim().toLowerCase() : '';
      if(!typed){ say(msid, 'Type the account name first', 'l'); return; }
      var mcode = nameToCode[typed];
      if(!mcode){ say(msid, 'No account with that exact name — pick from the list', 'l'); return; }
      linkLead(msid, mcode);
      return;
    }
    var lcf = t.closest('[data-leadconfirm]');
    if(lcf){ linkLead(lcf.getAttribute('data-leadconfirm'), lcf.getAttribute('data-code')); return; }
    var lc = t.closest('[data-leadclear]');
    if(lc){ linkLead(lc.getAttribute('data-leadclear'), ''); return; }
  });
  document.addEventListener('change', function(ev){
    var t = ev.target;
    if(t.matches('[data-lowner]')) logLead(t.getAttribute('data-lowner'), 'owner', {owner: t.value});
    else if(t.matches('[data-lverdict]')) logLead(t.getAttribute('data-lverdict'), 'verdict', {note: t.value});
    else if(t.matches('[data-lfudate]') && t.value)
      logLead(t.getAttribute('data-lfudate'), 'follow_up', {due: t.value});
  });
  document.addEventListener('keydown', function(ev){
    if(ev.key === 'Escape') closeNav();
  });
  ['leadstatus', 'leadowner', 'leadverdict', 'leadrating', 'leadexcl'].forEach(function(id){
    document.getElementById(id).addEventListener('change', renderLeads);
  });
  document.getElementById('leadq').addEventListener('input', renderLeads);
  document.getElementById('fumine').addEventListener('change', renderLeadRecs);
  window.addEventListener('hashchange', route);

  // ---------------------------------------------------------------- sign-in
  var NEXT_KEY = 'rushtons.leads.next';
  function signedOut(msg){
    document.getElementById('app').hidden = true;
    document.getElementById('signin').hidden = false;
    document.getElementById('signmsg').textContent = msg || '';
    // remember the lead an email link pointed at, for after sign-in
    var h = location.hash.slice(1);
    if(/^lead[\/-]/.test(h)){ try { localStorage.setItem(NEXT_KEY, h); } catch(err){} }
  }
  document.getElementById('signform').addEventListener('submit', function(ev){
    ev.preventDefault();
    var email = document.getElementById('signemail').value.trim().toLowerCase();
    var btn = document.getElementById('signbtn'), msg = document.getElementById('signmsg');
    if(!email) return;
    btn.disabled = true;
    msg.textContent = 'Sending…';
    sb.auth.signInWithOtp({email: email, options: {shouldCreateUser: true,
      emailRedirectTo: location.origin + location.pathname}}).then(function(res){
      btn.disabled = false;
      if(!res.error){
        msg.textContent = 'Check your inbox: we have emailed a sign-in link to '
          + email + '. Open it on this device.';
        return;
      }
      var m = res.error.message || '';
      msg.textContent = /database error|not on the/i.test(m)
        ? 'That email is not on the leads app list. Ask Jose to add you.'
        : /rate|seconds/i.test(m)
          ? 'Too many sign-in emails just now — wait a minute and try again.'
          : 'Could not send the link (' + m + ').';
    });
  });
  document.getElementById('signout').addEventListener('click', function(){
    sb.auth.signOut().then(function(){ location.hash = ''; location.reload(); });
  });

  var started = false;
  function start(session){
    if(started) return;
    started = true;
    sb.rpc('leads_app_lookups').then(function(res){
      if(res.error){
        started = false;
        sb.auth.signOut();
        signedOut(res.error.code === '42501'
          ? 'Your email is not on the leads app list. Ask Jose to add you.'
          : 'Could not load your account (' + res.error.message + ').');
        return;
      }
      var lk = res.data;
      ME = lk.me;
      DATA.leadTeam = lk.team;
      DATA.directory = lk.accounts.map(function(a){
        return {c: a[0], n: a[1], s: a[2] || '', last: a[3] || ''}; });
      nameToCode = {};
      DATA.directory.forEach(function(d){ nameToCode[d.n.toLowerCase()] = d.c; });
      document.getElementById('acctnames').innerHTML = DATA.directory.map(function(d){
        return '<option value="' + esc(d.n) + '"></option>'; }).join('');
      document.getElementById('who').textContent = ME + ' (' + session.user.email + ')';
      document.getElementById('signin').hidden = true;
      document.getElementById('app').hidden = false;
      initLeadFilters();
      var next = '';
      try { next = localStorage.getItem(NEXT_KEY) || ''; localStorage.removeItem(NEXT_KEY); } catch(err){}
      if(next){ try { history.replaceState(null, '', '#' + next); } catch(err){} }
      else if(/access_token/.test(location.hash)){
        try { history.replaceState(null, '', location.pathname); } catch(err){}
      }
      route();
      reload();
      // live: new leads and colleagues' changes arrive as they happen
      sb.channel('leads-app')
        .on('postgres_changes', {event: '*', schema: 'public', table: 'leads'}, scheduleReload)
        .on('postgres_changes', {event: '*', schema: 'public', table: 'lead_activities'}, scheduleReload)
        .subscribe();
      document.addEventListener('visibilitychange', function(){
        if(!document.hidden) scheduleReload(); });
      setInterval(scheduleReload, 5 * 60 * 1000);
    });
  }
  sb.auth.onAuthStateChange(function(event, session){
    if(session) start(session);
    else if(event === 'SIGNED_OUT') signedOut();
  });
  sb.auth.getSession().then(function(res){
    var session = res.data && res.data.session;
    if(session) start(session);
    else signedOut(/error_description/.test(location.hash)
      ? 'That sign-in link has expired or was already used. Ask for a new one.' : '');
  });
})();
