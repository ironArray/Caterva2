var L = "top", P = "bottom", M = "right", I = "left", ge = "auto", Rt = [L, P, M, I], _t = "start", St = "end", ss = "clippingParents", Ge = "viewport", wt = "popper", is = "reference", je = /* @__PURE__ */ Rt.reduce(function(n, t) {
  return n.concat([t + "-" + _t, t + "-" + St]);
}, []), qe = /* @__PURE__ */ [].concat(Rt, [ge]).reduce(function(n, t) {
  return n.concat([t, t + "-" + _t, t + "-" + St]);
}, []), rs = "beforeRead", os = "read", as = "afterRead", cs = "beforeMain", ls = "main", us = "afterMain", ds = "beforeWrite", hs = "write", fs = "afterWrite", ps = [rs, os, as, cs, ls, us, ds, hs, fs];
function z(n) {
  return n ? (n.nodeName || "").toLowerCase() : null;
}
function k(n) {
  if (n == null)
    return window;
  if (n.toString() !== "[object Window]") {
    var t = n.ownerDocument;
    return t && t.defaultView || window;
  }
  return n;
}
function mt(n) {
  var t = k(n).Element;
  return n instanceof t || n instanceof Element;
}
function V(n) {
  var t = k(n).HTMLElement;
  return n instanceof t || n instanceof HTMLElement;
}
function Xe(n) {
  if (typeof ShadowRoot > "u")
    return !1;
  var t = k(n).ShadowRoot;
  return n instanceof t || n instanceof ShadowRoot;
}
function ui(n) {
  var t = n.state;
  Object.keys(t.elements).forEach(function(e) {
    var s = t.styles[e] || {}, i = t.attributes[e] || {}, r = t.elements[e];
    !V(r) || !z(r) || (Object.assign(r.style, s), Object.keys(i).forEach(function(o) {
      var a = i[o];
      a === !1 ? r.removeAttribute(o) : r.setAttribute(o, a === !0 ? "" : a);
    }));
  });
}
function di(n) {
  var t = n.state, e = {
    popper: {
      position: t.options.strategy,
      left: "0",
      top: "0",
      margin: "0"
    },
    arrow: {
      position: "absolute"
    },
    reference: {}
  };
  return Object.assign(t.elements.popper.style, e.popper), t.styles = e, t.elements.arrow && Object.assign(t.elements.arrow.style, e.arrow), function() {
    Object.keys(t.elements).forEach(function(s) {
      var i = t.elements[s], r = t.attributes[s] || {}, o = Object.keys(t.styles.hasOwnProperty(s) ? t.styles[s] : e[s]), a = o.reduce(function(c, d) {
        return c[d] = "", c;
      }, {});
      !V(i) || !z(i) || (Object.assign(i.style, a), Object.keys(r).forEach(function(c) {
        i.removeAttribute(c);
      }));
    });
  };
}
const Qe = {
  name: "applyStyles",
  enabled: !0,
  phase: "write",
  fn: ui,
  effect: di,
  requires: ["computeStyles"]
};
function U(n) {
  return n.split("-")[0];
}
var pt = Math.max, fe = Math.min, Dt = Math.round;
function Be() {
  var n = navigator.userAgentData;
  return n != null && n.brands && Array.isArray(n.brands) ? n.brands.map(function(t) {
    return t.brand + "/" + t.version;
  }).join(" ") : navigator.userAgent;
}
function _s() {
  return !/^((?!chrome|android).)*safari/i.test(Be());
}
function $t(n, t, e) {
  t === void 0 && (t = !1), e === void 0 && (e = !1);
  var s = n.getBoundingClientRect(), i = 1, r = 1;
  t && V(n) && (i = n.offsetWidth > 0 && Dt(s.width) / n.offsetWidth || 1, r = n.offsetHeight > 0 && Dt(s.height) / n.offsetHeight || 1);
  var o = mt(n) ? k(n) : window, a = o.visualViewport, c = !_s() && e, d = (s.left + (c && a ? a.offsetLeft : 0)) / i, u = (s.top + (c && a ? a.offsetTop : 0)) / r, f = s.width / i, _ = s.height / r;
  return {
    width: f,
    height: _,
    top: u,
    right: d + f,
    bottom: u + _,
    left: d,
    x: d,
    y: u
  };
}
function Je(n) {
  var t = $t(n), e = n.offsetWidth, s = n.offsetHeight;
  return Math.abs(t.width - e) <= 1 && (e = t.width), Math.abs(t.height - s) <= 1 && (s = t.height), {
    x: n.offsetLeft,
    y: n.offsetTop,
    width: e,
    height: s
  };
}
function ms(n, t) {
  var e = t.getRootNode && t.getRootNode();
  if (n.contains(t))
    return !0;
  if (e && Xe(e)) {
    var s = t;
    do {
      if (s && n.isSameNode(s))
        return !0;
      s = s.parentNode || s.host;
    } while (s);
  }
  return !1;
}
function X(n) {
  return k(n).getComputedStyle(n);
}
function hi(n) {
  return ["table", "td", "th"].indexOf(z(n)) >= 0;
}
function it(n) {
  return ((mt(n) ? n.ownerDocument : (
    // $FlowFixMe[prop-missing]
    n.document
  )) || window.document).documentElement;
}
function Ee(n) {
  return z(n) === "html" ? n : (
    // this is a quicker (but less type safe) way to save quite some bytes from the bundle
    // $FlowFixMe[incompatible-return]
    // $FlowFixMe[prop-missing]
    n.assignedSlot || // step into the shadow DOM of the parent of a slotted node
    n.parentNode || // DOM Element detected
    (Xe(n) ? n.host : null) || // ShadowRoot detected
    // $FlowFixMe[incompatible-call]: HTMLElement is a Node
    it(n)
  );
}
function An(n) {
  return !V(n) || // https://github.com/popperjs/popper-core/issues/837
  X(n).position === "fixed" ? null : n.offsetParent;
}
function fi(n) {
  var t = /firefox/i.test(Be()), e = /Trident/i.test(Be());
  if (e && V(n)) {
    var s = X(n);
    if (s.position === "fixed")
      return null;
  }
  var i = Ee(n);
  for (Xe(i) && (i = i.host); V(i) && ["html", "body"].indexOf(z(i)) < 0; ) {
    var r = X(i);
    if (r.transform !== "none" || r.perspective !== "none" || r.contain === "paint" || ["transform", "perspective"].indexOf(r.willChange) !== -1 || t && r.willChange === "filter" || t && r.filter && r.filter !== "none")
      return i;
    i = i.parentNode;
  }
  return null;
}
function Kt(n) {
  for (var t = k(n), e = An(n); e && hi(e) && X(e).position === "static"; )
    e = An(e);
  return e && (z(e) === "html" || z(e) === "body" && X(e).position === "static") ? t : e || fi(n) || t;
}
function Ze(n) {
  return ["top", "bottom"].indexOf(n) >= 0 ? "x" : "y";
}
function Bt(n, t, e) {
  return pt(n, fe(t, e));
}
function pi(n, t, e) {
  var s = Bt(n, t, e);
  return s > e ? e : s;
}
function gs() {
  return {
    top: 0,
    right: 0,
    bottom: 0,
    left: 0
  };
}
function Es(n) {
  return Object.assign({}, gs(), n);
}
function vs(n, t) {
  return t.reduce(function(e, s) {
    return e[s] = n, e;
  }, {});
}
var _i = function(t, e) {
  return t = typeof t == "function" ? t(Object.assign({}, e.rects, {
    placement: e.placement
  })) : t, Es(typeof t != "number" ? t : vs(t, Rt));
};
function mi(n) {
  var t, e = n.state, s = n.name, i = n.options, r = e.elements.arrow, o = e.modifiersData.popperOffsets, a = U(e.placement), c = Ze(a), d = [I, M].indexOf(a) >= 0, u = d ? "height" : "width";
  if (!(!r || !o)) {
    var f = _i(i.padding, e), _ = Je(r), p = c === "y" ? L : I, A = c === "y" ? P : M, m = e.rects.reference[u] + e.rects.reference[c] - o[c] - e.rects.popper[u], E = o[c] - e.rects.reference[c], T = Kt(r), w = T ? c === "y" ? T.clientHeight || 0 : T.clientWidth || 0 : 0, O = m / 2 - E / 2, g = f[p], v = w - _[u] - f[A], b = w / 2 - _[u] / 2 + O, y = Bt(g, b, v), S = c;
    e.modifiersData[s] = (t = {}, t[S] = y, t.centerOffset = y - b, t);
  }
}
function gi(n) {
  var t = n.state, e = n.options, s = e.element, i = s === void 0 ? "[data-popper-arrow]" : s;
  i != null && (typeof i == "string" && (i = t.elements.popper.querySelector(i), !i) || ms(t.elements.popper, i) && (t.elements.arrow = i));
}
const bs = {
  name: "arrow",
  enabled: !0,
  phase: "main",
  fn: mi,
  effect: gi,
  requires: ["popperOffsets"],
  requiresIfExists: ["preventOverflow"]
};
function Lt(n) {
  return n.split("-")[1];
}
var Ei = {
  top: "auto",
  right: "auto",
  bottom: "auto",
  left: "auto"
};
function vi(n, t) {
  var e = n.x, s = n.y, i = t.devicePixelRatio || 1;
  return {
    x: Dt(e * i) / i || 0,
    y: Dt(s * i) / i || 0
  };
}
function Tn(n) {
  var t, e = n.popper, s = n.popperRect, i = n.placement, r = n.variation, o = n.offsets, a = n.position, c = n.gpuAcceleration, d = n.adaptive, u = n.roundOffsets, f = n.isFixed, _ = o.x, p = _ === void 0 ? 0 : _, A = o.y, m = A === void 0 ? 0 : A, E = typeof u == "function" ? u({
    x: p,
    y: m
  }) : {
    x: p,
    y: m
  };
  p = E.x, m = E.y;
  var T = o.hasOwnProperty("x"), w = o.hasOwnProperty("y"), O = I, g = L, v = window;
  if (d) {
    var b = Kt(e), y = "clientHeight", S = "clientWidth";
    if (b === k(e) && (b = it(e), X(b).position !== "static" && a === "absolute" && (y = "scrollHeight", S = "scrollWidth")), b = b, i === L || (i === I || i === M) && r === St) {
      g = P;
      var N = f && b === v && v.visualViewport ? v.visualViewport.height : (
        // $FlowFixMe[prop-missing]
        b[y]
      );
      m -= N - s.height, m *= c ? 1 : -1;
    }
    if (i === I || (i === L || i === P) && r === St) {
      O = M;
      var C = f && b === v && v.visualViewport ? v.visualViewport.width : (
        // $FlowFixMe[prop-missing]
        b[S]
      );
      p -= C - s.width, p *= c ? 1 : -1;
    }
  }
  var D = Object.assign({
    position: a
  }, d && Ei), B = u === !0 ? vi({
    x: p,
    y: m
  }, k(e)) : {
    x: p,
    y: m
  };
  if (p = B.x, m = B.y, c) {
    var $;
    return Object.assign({}, D, ($ = {}, $[g] = w ? "0" : "", $[O] = T ? "0" : "", $.transform = (v.devicePixelRatio || 1) <= 1 ? "translate(" + p + "px, " + m + "px)" : "translate3d(" + p + "px, " + m + "px, 0)", $));
  }
  return Object.assign({}, D, (t = {}, t[g] = w ? m + "px" : "", t[O] = T ? p + "px" : "", t.transform = "", t));
}
function bi(n) {
  var t = n.state, e = n.options, s = e.gpuAcceleration, i = s === void 0 ? !0 : s, r = e.adaptive, o = r === void 0 ? !0 : r, a = e.roundOffsets, c = a === void 0 ? !0 : a, d = {
    placement: U(t.placement),
    variation: Lt(t.placement),
    popper: t.elements.popper,
    popperRect: t.rects.popper,
    gpuAcceleration: i,
    isFixed: t.options.strategy === "fixed"
  };
  t.modifiersData.popperOffsets != null && (t.styles.popper = Object.assign({}, t.styles.popper, Tn(Object.assign({}, d, {
    offsets: t.modifiersData.popperOffsets,
    position: t.options.strategy,
    adaptive: o,
    roundOffsets: c
  })))), t.modifiersData.arrow != null && (t.styles.arrow = Object.assign({}, t.styles.arrow, Tn(Object.assign({}, d, {
    offsets: t.modifiersData.arrow,
    position: "absolute",
    adaptive: !1,
    roundOffsets: c
  })))), t.attributes.popper = Object.assign({}, t.attributes.popper, {
    "data-popper-placement": t.placement
  });
}
const tn = {
  name: "computeStyles",
  enabled: !0,
  phase: "beforeWrite",
  fn: bi,
  data: {}
};
var se = {
  passive: !0
};
function Ai(n) {
  var t = n.state, e = n.instance, s = n.options, i = s.scroll, r = i === void 0 ? !0 : i, o = s.resize, a = o === void 0 ? !0 : o, c = k(t.elements.popper), d = [].concat(t.scrollParents.reference, t.scrollParents.popper);
  return r && d.forEach(function(u) {
    u.addEventListener("scroll", e.update, se);
  }), a && c.addEventListener("resize", e.update, se), function() {
    r && d.forEach(function(u) {
      u.removeEventListener("scroll", e.update, se);
    }), a && c.removeEventListener("resize", e.update, se);
  };
}
const en = {
  name: "eventListeners",
  enabled: !0,
  phase: "write",
  fn: function() {
  },
  effect: Ai,
  data: {}
};
var Ti = {
  left: "right",
  right: "left",
  bottom: "top",
  top: "bottom"
};
function ue(n) {
  return n.replace(/left|right|bottom|top/g, function(t) {
    return Ti[t];
  });
}
var yi = {
  start: "end",
  end: "start"
};
function yn(n) {
  return n.replace(/start|end/g, function(t) {
    return yi[t];
  });
}
function nn(n) {
  var t = k(n), e = t.pageXOffset, s = t.pageYOffset;
  return {
    scrollLeft: e,
    scrollTop: s
  };
}
function sn(n) {
  return $t(it(n)).left + nn(n).scrollLeft;
}
function wi(n, t) {
  var e = k(n), s = it(n), i = e.visualViewport, r = s.clientWidth, o = s.clientHeight, a = 0, c = 0;
  if (i) {
    r = i.width, o = i.height;
    var d = _s();
    (d || !d && t === "fixed") && (a = i.offsetLeft, c = i.offsetTop);
  }
  return {
    width: r,
    height: o,
    x: a + sn(n),
    y: c
  };
}
function Oi(n) {
  var t, e = it(n), s = nn(n), i = (t = n.ownerDocument) == null ? void 0 : t.body, r = pt(e.scrollWidth, e.clientWidth, i ? i.scrollWidth : 0, i ? i.clientWidth : 0), o = pt(e.scrollHeight, e.clientHeight, i ? i.scrollHeight : 0, i ? i.clientHeight : 0), a = -s.scrollLeft + sn(n), c = -s.scrollTop;
  return X(i || e).direction === "rtl" && (a += pt(e.clientWidth, i ? i.clientWidth : 0) - r), {
    width: r,
    height: o,
    x: a,
    y: c
  };
}
function rn(n) {
  var t = X(n), e = t.overflow, s = t.overflowX, i = t.overflowY;
  return /auto|scroll|overlay|hidden/.test(e + i + s);
}
function As(n) {
  return ["html", "body", "#document"].indexOf(z(n)) >= 0 ? n.ownerDocument.body : V(n) && rn(n) ? n : As(Ee(n));
}
function Ft(n, t) {
  var e;
  t === void 0 && (t = []);
  var s = As(n), i = s === ((e = n.ownerDocument) == null ? void 0 : e.body), r = k(s), o = i ? [r].concat(r.visualViewport || [], rn(s) ? s : []) : s, a = t.concat(o);
  return i ? a : (
    // $FlowFixMe[incompatible-call]: isBody tells us target will be an HTMLElement here
    a.concat(Ft(Ee(o)))
  );
}
function Fe(n) {
  return Object.assign({}, n, {
    left: n.x,
    top: n.y,
    right: n.x + n.width,
    bottom: n.y + n.height
  });
}
function Ci(n, t) {
  var e = $t(n, !1, t === "fixed");
  return e.top = e.top + n.clientTop, e.left = e.left + n.clientLeft, e.bottom = e.top + n.clientHeight, e.right = e.left + n.clientWidth, e.width = n.clientWidth, e.height = n.clientHeight, e.x = e.left, e.y = e.top, e;
}
function wn(n, t, e) {
  return t === Ge ? Fe(wi(n, e)) : mt(t) ? Ci(t, e) : Fe(Oi(it(n)));
}
function Ni(n) {
  var t = Ft(Ee(n)), e = ["absolute", "fixed"].indexOf(X(n).position) >= 0, s = e && V(n) ? Kt(n) : n;
  return mt(s) ? t.filter(function(i) {
    return mt(i) && ms(i, s) && z(i) !== "body";
  }) : [];
}
function Si(n, t, e, s) {
  var i = t === "clippingParents" ? Ni(n) : [].concat(t), r = [].concat(i, [e]), o = r[0], a = r.reduce(function(c, d) {
    var u = wn(n, d, s);
    return c.top = pt(u.top, c.top), c.right = fe(u.right, c.right), c.bottom = fe(u.bottom, c.bottom), c.left = pt(u.left, c.left), c;
  }, wn(n, o, s));
  return a.width = a.right - a.left, a.height = a.bottom - a.top, a.x = a.left, a.y = a.top, a;
}
function Ts(n) {
  var t = n.reference, e = n.element, s = n.placement, i = s ? U(s) : null, r = s ? Lt(s) : null, o = t.x + t.width / 2 - e.width / 2, a = t.y + t.height / 2 - e.height / 2, c;
  switch (i) {
    case L:
      c = {
        x: o,
        y: t.y - e.height
      };
      break;
    case P:
      c = {
        x: o,
        y: t.y + t.height
      };
      break;
    case M:
      c = {
        x: t.x + t.width,
        y: a
      };
      break;
    case I:
      c = {
        x: t.x - e.width,
        y: a
      };
      break;
    default:
      c = {
        x: t.x,
        y: t.y
      };
  }
  var d = i ? Ze(i) : null;
  if (d != null) {
    var u = d === "y" ? "height" : "width";
    switch (r) {
      case _t:
        c[d] = c[d] - (t[u] / 2 - e[u] / 2);
        break;
      case St:
        c[d] = c[d] + (t[u] / 2 - e[u] / 2);
        break;
    }
  }
  return c;
}
function It(n, t) {
  t === void 0 && (t = {});
  var e = t, s = e.placement, i = s === void 0 ? n.placement : s, r = e.strategy, o = r === void 0 ? n.strategy : r, a = e.boundary, c = a === void 0 ? ss : a, d = e.rootBoundary, u = d === void 0 ? Ge : d, f = e.elementContext, _ = f === void 0 ? wt : f, p = e.altBoundary, A = p === void 0 ? !1 : p, m = e.padding, E = m === void 0 ? 0 : m, T = Es(typeof E != "number" ? E : vs(E, Rt)), w = _ === wt ? is : wt, O = n.rects.popper, g = n.elements[A ? w : _], v = Si(mt(g) ? g : g.contextElement || it(n.elements.popper), c, u, o), b = $t(n.elements.reference), y = Ts({
    reference: b,
    element: O,
    strategy: "absolute",
    placement: i
  }), S = Fe(Object.assign({}, O, y)), N = _ === wt ? S : b, C = {
    top: v.top - N.top + T.top,
    bottom: N.bottom - v.bottom + T.bottom,
    left: v.left - N.left + T.left,
    right: N.right - v.right + T.right
  }, D = n.modifiersData.offset;
  if (_ === wt && D) {
    var B = D[i];
    Object.keys(C).forEach(function($) {
      var at = [M, P].indexOf($) >= 0 ? 1 : -1, ct = [L, P].indexOf($) >= 0 ? "y" : "x";
      C[$] += B[ct] * at;
    });
  }
  return C;
}
function Di(n, t) {
  t === void 0 && (t = {});
  var e = t, s = e.placement, i = e.boundary, r = e.rootBoundary, o = e.padding, a = e.flipVariations, c = e.allowedAutoPlacements, d = c === void 0 ? qe : c, u = Lt(s), f = u ? a ? je : je.filter(function(A) {
    return Lt(A) === u;
  }) : Rt, _ = f.filter(function(A) {
    return d.indexOf(A) >= 0;
  });
  _.length === 0 && (_ = f);
  var p = _.reduce(function(A, m) {
    return A[m] = It(n, {
      placement: m,
      boundary: i,
      rootBoundary: r,
      padding: o
    })[U(m)], A;
  }, {});
  return Object.keys(p).sort(function(A, m) {
    return p[A] - p[m];
  });
}
function $i(n) {
  if (U(n) === ge)
    return [];
  var t = ue(n);
  return [yn(n), t, yn(t)];
}
function Li(n) {
  var t = n.state, e = n.options, s = n.name;
  if (!t.modifiersData[s]._skip) {
    for (var i = e.mainAxis, r = i === void 0 ? !0 : i, o = e.altAxis, a = o === void 0 ? !0 : o, c = e.fallbackPlacements, d = e.padding, u = e.boundary, f = e.rootBoundary, _ = e.altBoundary, p = e.flipVariations, A = p === void 0 ? !0 : p, m = e.allowedAutoPlacements, E = t.options.placement, T = U(E), w = T === E, O = c || (w || !A ? [ue(E)] : $i(E)), g = [E].concat(O).reduce(function(At, Z) {
      return At.concat(U(Z) === ge ? Di(t, {
        placement: Z,
        boundary: u,
        rootBoundary: f,
        padding: d,
        flipVariations: A,
        allowedAutoPlacements: m
      }) : Z);
    }, []), v = t.rects.reference, b = t.rects.popper, y = /* @__PURE__ */ new Map(), S = !0, N = g[0], C = 0; C < g.length; C++) {
      var D = g[C], B = U(D), $ = Lt(D) === _t, at = [L, P].indexOf(B) >= 0, ct = at ? "width" : "height", R = It(t, {
        placement: D,
        boundary: u,
        rootBoundary: f,
        altBoundary: _,
        padding: d
      }), F = at ? $ ? M : I : $ ? P : L;
      v[ct] > b[ct] && (F = ue(F));
      var Jt = ue(F), lt = [];
      if (r && lt.push(R[B] <= 0), a && lt.push(R[F] <= 0, R[Jt] <= 0), lt.every(function(At) {
        return At;
      })) {
        N = D, S = !1;
        break;
      }
      y.set(D, lt);
    }
    if (S)
      for (var Zt = A ? 3 : 1, Te = function(Z) {
        var Ht = g.find(function(ee) {
          var ut = y.get(ee);
          if (ut)
            return ut.slice(0, Z).every(function(ye) {
              return ye;
            });
        });
        if (Ht)
          return N = Ht, "break";
      }, Vt = Zt; Vt > 0; Vt--) {
        var te = Te(Vt);
        if (te === "break")
          break;
      }
    t.placement !== N && (t.modifiersData[s]._skip = !0, t.placement = N, t.reset = !0);
  }
}
const ys = {
  name: "flip",
  enabled: !0,
  phase: "main",
  fn: Li,
  requiresIfExists: ["offset"],
  data: {
    _skip: !1
  }
};
function On(n, t, e) {
  return e === void 0 && (e = {
    x: 0,
    y: 0
  }), {
    top: n.top - t.height - e.y,
    right: n.right - t.width + e.x,
    bottom: n.bottom - t.height + e.y,
    left: n.left - t.width - e.x
  };
}
function Cn(n) {
  return [L, M, P, I].some(function(t) {
    return n[t] >= 0;
  });
}
function Ii(n) {
  var t = n.state, e = n.name, s = t.rects.reference, i = t.rects.popper, r = t.modifiersData.preventOverflow, o = It(t, {
    elementContext: "reference"
  }), a = It(t, {
    altBoundary: !0
  }), c = On(o, s), d = On(a, i, r), u = Cn(c), f = Cn(d);
  t.modifiersData[e] = {
    referenceClippingOffsets: c,
    popperEscapeOffsets: d,
    isReferenceHidden: u,
    hasPopperEscaped: f
  }, t.attributes.popper = Object.assign({}, t.attributes.popper, {
    "data-popper-reference-hidden": u,
    "data-popper-escaped": f
  });
}
const ws = {
  name: "hide",
  enabled: !0,
  phase: "main",
  requiresIfExists: ["preventOverflow"],
  fn: Ii
};
function xi(n, t, e) {
  var s = U(n), i = [I, L].indexOf(s) >= 0 ? -1 : 1, r = typeof e == "function" ? e(Object.assign({}, t, {
    placement: n
  })) : e, o = r[0], a = r[1];
  return o = o || 0, a = (a || 0) * i, [I, M].indexOf(s) >= 0 ? {
    x: a,
    y: o
  } : {
    x: o,
    y: a
  };
}
function Ri(n) {
  var t = n.state, e = n.options, s = n.name, i = e.offset, r = i === void 0 ? [0, 0] : i, o = qe.reduce(function(u, f) {
    return u[f] = xi(f, t.rects, r), u;
  }, {}), a = o[t.placement], c = a.x, d = a.y;
  t.modifiersData.popperOffsets != null && (t.modifiersData.popperOffsets.x += c, t.modifiersData.popperOffsets.y += d), t.modifiersData[s] = o;
}
const Os = {
  name: "offset",
  enabled: !0,
  phase: "main",
  requires: ["popperOffsets"],
  fn: Ri
};
function Pi(n) {
  var t = n.state, e = n.name;
  t.modifiersData[e] = Ts({
    reference: t.rects.reference,
    element: t.rects.popper,
    strategy: "absolute",
    placement: t.placement
  });
}
const on = {
  name: "popperOffsets",
  enabled: !0,
  phase: "read",
  fn: Pi,
  data: {}
};
function Mi(n) {
  return n === "x" ? "y" : "x";
}
function ki(n) {
  var t = n.state, e = n.options, s = n.name, i = e.mainAxis, r = i === void 0 ? !0 : i, o = e.altAxis, a = o === void 0 ? !1 : o, c = e.boundary, d = e.rootBoundary, u = e.altBoundary, f = e.padding, _ = e.tether, p = _ === void 0 ? !0 : _, A = e.tetherOffset, m = A === void 0 ? 0 : A, E = It(t, {
    boundary: c,
    rootBoundary: d,
    padding: f,
    altBoundary: u
  }), T = U(t.placement), w = Lt(t.placement), O = !w, g = Ze(T), v = Mi(g), b = t.modifiersData.popperOffsets, y = t.rects.reference, S = t.rects.popper, N = typeof m == "function" ? m(Object.assign({}, t.rects, {
    placement: t.placement
  })) : m, C = typeof N == "number" ? {
    mainAxis: N,
    altAxis: N
  } : Object.assign({
    mainAxis: 0,
    altAxis: 0
  }, N), D = t.modifiersData.offset ? t.modifiersData.offset[t.placement] : null, B = {
    x: 0,
    y: 0
  };
  if (b) {
    if (r) {
      var $, at = g === "y" ? L : I, ct = g === "y" ? P : M, R = g === "y" ? "height" : "width", F = b[g], Jt = F + E[at], lt = F - E[ct], Zt = p ? -S[R] / 2 : 0, Te = w === _t ? y[R] : S[R], Vt = w === _t ? -S[R] : -y[R], te = t.elements.arrow, At = p && te ? Je(te) : {
        width: 0,
        height: 0
      }, Z = t.modifiersData["arrow#persistent"] ? t.modifiersData["arrow#persistent"].padding : gs(), Ht = Z[at], ee = Z[ct], ut = Bt(0, y[R], At[R]), ye = O ? y[R] / 2 - Zt - ut - Ht - C.mainAxis : Te - ut - Ht - C.mainAxis, ii = O ? -y[R] / 2 + Zt + ut + ee + C.mainAxis : Vt + ut + ee + C.mainAxis, we = t.elements.arrow && Kt(t.elements.arrow), ri = we ? g === "y" ? we.clientTop || 0 : we.clientLeft || 0 : 0, hn = ($ = D == null ? void 0 : D[g]) != null ? $ : 0, oi = F + ye - hn - ri, ai = F + ii - hn, fn = Bt(p ? fe(Jt, oi) : Jt, F, p ? pt(lt, ai) : lt);
      b[g] = fn, B[g] = fn - F;
    }
    if (a) {
      var pn, ci = g === "x" ? L : I, li = g === "x" ? P : M, dt = b[v], ne = v === "y" ? "height" : "width", _n = dt + E[ci], mn = dt - E[li], Oe = [L, I].indexOf(T) !== -1, gn = (pn = D == null ? void 0 : D[v]) != null ? pn : 0, En = Oe ? _n : dt - y[ne] - S[ne] - gn + C.altAxis, vn = Oe ? dt + y[ne] + S[ne] - gn - C.altAxis : mn, bn = p && Oe ? pi(En, dt, vn) : Bt(p ? En : _n, dt, p ? vn : mn);
      b[v] = bn, B[v] = bn - dt;
    }
    t.modifiersData[s] = B;
  }
}
const Cs = {
  name: "preventOverflow",
  enabled: !0,
  phase: "main",
  fn: ki,
  requiresIfExists: ["offset"]
};
function Vi(n) {
  return {
    scrollLeft: n.scrollLeft,
    scrollTop: n.scrollTop
  };
}
function Hi(n) {
  return n === k(n) || !V(n) ? nn(n) : Vi(n);
}
function Wi(n) {
  var t = n.getBoundingClientRect(), e = Dt(t.width) / n.offsetWidth || 1, s = Dt(t.height) / n.offsetHeight || 1;
  return e !== 1 || s !== 1;
}
function ji(n, t, e) {
  e === void 0 && (e = !1);
  var s = V(t), i = V(t) && Wi(t), r = it(t), o = $t(n, i, e), a = {
    scrollLeft: 0,
    scrollTop: 0
  }, c = {
    x: 0,
    y: 0
  };
  return (s || !s && !e) && ((z(t) !== "body" || // https://github.com/popperjs/popper-core/issues/1078
  rn(r)) && (a = Hi(t)), V(t) ? (c = $t(t, !0), c.x += t.clientLeft, c.y += t.clientTop) : r && (c.x = sn(r))), {
    x: o.left + a.scrollLeft - c.x,
    y: o.top + a.scrollTop - c.y,
    width: o.width,
    height: o.height
  };
}
function Bi(n) {
  var t = /* @__PURE__ */ new Map(), e = /* @__PURE__ */ new Set(), s = [];
  n.forEach(function(r) {
    t.set(r.name, r);
  });
  function i(r) {
    e.add(r.name);
    var o = [].concat(r.requires || [], r.requiresIfExists || []);
    o.forEach(function(a) {
      if (!e.has(a)) {
        var c = t.get(a);
        c && i(c);
      }
    }), s.push(r);
  }
  return n.forEach(function(r) {
    e.has(r.name) || i(r);
  }), s;
}
function Fi(n) {
  var t = Bi(n);
  return ps.reduce(function(e, s) {
    return e.concat(t.filter(function(i) {
      return i.phase === s;
    }));
  }, []);
}
function Ki(n) {
  var t;
  return function() {
    return t || (t = new Promise(function(e) {
      Promise.resolve().then(function() {
        t = void 0, e(n());
      });
    })), t;
  };
}
function Yi(n) {
  var t = n.reduce(function(e, s) {
    var i = e[s.name];
    return e[s.name] = i ? Object.assign({}, i, s, {
      options: Object.assign({}, i.options, s.options),
      data: Object.assign({}, i.data, s.data)
    }) : s, e;
  }, {});
  return Object.keys(t).map(function(e) {
    return t[e];
  });
}
var Nn = {
  placement: "bottom",
  modifiers: [],
  strategy: "absolute"
};
function Sn() {
  for (var n = arguments.length, t = new Array(n), e = 0; e < n; e++)
    t[e] = arguments[e];
  return !t.some(function(s) {
    return !(s && typeof s.getBoundingClientRect == "function");
  });
}
function ve(n) {
  n === void 0 && (n = {});
  var t = n, e = t.defaultModifiers, s = e === void 0 ? [] : e, i = t.defaultOptions, r = i === void 0 ? Nn : i;
  return function(a, c, d) {
    d === void 0 && (d = r);
    var u = {
      placement: "bottom",
      orderedModifiers: [],
      options: Object.assign({}, Nn, r),
      modifiersData: {},
      elements: {
        reference: a,
        popper: c
      },
      attributes: {},
      styles: {}
    }, f = [], _ = !1, p = {
      state: u,
      setOptions: function(T) {
        var w = typeof T == "function" ? T(u.options) : T;
        m(), u.options = Object.assign({}, r, u.options, w), u.scrollParents = {
          reference: mt(a) ? Ft(a) : a.contextElement ? Ft(a.contextElement) : [],
          popper: Ft(c)
        };
        var O = Fi(Yi([].concat(s, u.options.modifiers)));
        return u.orderedModifiers = O.filter(function(g) {
          return g.enabled;
        }), A(), p.update();
      },
      // Sync update – it will always be executed, even if not necessary. This
      // is useful for low frequency updates where sync behavior simplifies the
      // logic.
      // For high frequency updates (e.g. `resize` and `scroll` events), always
      // prefer the async Popper#update method
      forceUpdate: function() {
        if (!_) {
          var T = u.elements, w = T.reference, O = T.popper;
          if (Sn(w, O)) {
            u.rects = {
              reference: ji(w, Kt(O), u.options.strategy === "fixed"),
              popper: Je(O)
            }, u.reset = !1, u.placement = u.options.placement, u.orderedModifiers.forEach(function(C) {
              return u.modifiersData[C.name] = Object.assign({}, C.data);
            });
            for (var g = 0; g < u.orderedModifiers.length; g++) {
              if (u.reset === !0) {
                u.reset = !1, g = -1;
                continue;
              }
              var v = u.orderedModifiers[g], b = v.fn, y = v.options, S = y === void 0 ? {} : y, N = v.name;
              typeof b == "function" && (u = b({
                state: u,
                options: S,
                name: N,
                instance: p
              }) || u);
            }
          }
        }
      },
      // Async and optimistically optimized update – it will not be executed if
      // not necessary (debounced to run at most once-per-tick)
      update: Ki(function() {
        return new Promise(function(E) {
          p.forceUpdate(), E(u);
        });
      }),
      destroy: function() {
        m(), _ = !0;
      }
    };
    if (!Sn(a, c))
      return p;
    p.setOptions(d).then(function(E) {
      !_ && d.onFirstUpdate && d.onFirstUpdate(E);
    });
    function A() {
      u.orderedModifiers.forEach(function(E) {
        var T = E.name, w = E.options, O = w === void 0 ? {} : w, g = E.effect;
        if (typeof g == "function") {
          var v = g({
            state: u,
            name: T,
            instance: p,
            options: O
          }), b = function() {
          };
          f.push(v || b);
        }
      });
    }
    function m() {
      f.forEach(function(E) {
        return E();
      }), f = [];
    }
    return p;
  };
}
var Ui = /* @__PURE__ */ ve(), zi = [en, on, tn, Qe], Gi = /* @__PURE__ */ ve({
  defaultModifiers: zi
}), qi = [en, on, tn, Qe, Os, ys, Cs, bs, ws], an = /* @__PURE__ */ ve({
  defaultModifiers: qi
});
const Ns = /* @__PURE__ */ Object.freeze(/* @__PURE__ */ Object.defineProperty({
  __proto__: null,
  afterMain: us,
  afterRead: as,
  afterWrite: fs,
  applyStyles: Qe,
  arrow: bs,
  auto: ge,
  basePlacements: Rt,
  beforeMain: cs,
  beforeRead: rs,
  beforeWrite: ds,
  bottom: P,
  clippingParents: ss,
  computeStyles: tn,
  createPopper: an,
  createPopperBase: Ui,
  createPopperLite: Gi,
  detectOverflow: It,
  end: St,
  eventListeners: en,
  flip: ys,
  hide: ws,
  left: I,
  main: ls,
  modifierPhases: ps,
  offset: Os,
  placements: qe,
  popper: wt,
  popperGenerator: ve,
  popperOffsets: on,
  preventOverflow: Cs,
  read: os,
  reference: is,
  right: M,
  start: _t,
  top: L,
  variationPlacements: je,
  viewport: Ge,
  write: hs
}, Symbol.toStringTag, { value: "Module" }));
/*!
  * Bootstrap v5.3.3 (https://getbootstrap.com/)
  * Copyright 2011-2024 The Bootstrap Authors (https://github.com/twbs/bootstrap/graphs/contributors)
  * Licensed under MIT (https://github.com/twbs/bootstrap/blob/main/LICENSE)
  */
const tt = /* @__PURE__ */ new Map(), Ce = {
  set(n, t, e) {
    tt.has(n) || tt.set(n, /* @__PURE__ */ new Map());
    const s = tt.get(n);
    if (!s.has(t) && s.size !== 0) {
      console.error(`Bootstrap doesn't allow more than one instance per element. Bound instance: ${Array.from(s.keys())[0]}.`);
      return;
    }
    s.set(t, e);
  },
  get(n, t) {
    return tt.has(n) && tt.get(n).get(t) || null;
  },
  remove(n, t) {
    if (!tt.has(n))
      return;
    const e = tt.get(n);
    e.delete(t), e.size === 0 && tt.delete(n);
  }
}, Xi = 1e6, Qi = 1e3, Ke = "transitionend", Ss = (n) => (n && window.CSS && window.CSS.escape && (n = n.replace(/#([^\s"#']+)/g, (t, e) => `#${CSS.escape(e)}`)), n), Ji = (n) => n == null ? `${n}` : Object.prototype.toString.call(n).match(/\s([a-z]+)/i)[1].toLowerCase(), Zi = (n) => {
  do
    n += Math.floor(Math.random() * Xi);
  while (document.getElementById(n));
  return n;
}, tr = (n) => {
  if (!n)
    return 0;
  let {
    transitionDuration: t,
    transitionDelay: e
  } = window.getComputedStyle(n);
  const s = Number.parseFloat(t), i = Number.parseFloat(e);
  return !s && !i ? 0 : (t = t.split(",")[0], e = e.split(",")[0], (Number.parseFloat(t) + Number.parseFloat(e)) * Qi);
}, Ds = (n) => {
  n.dispatchEvent(new Event(Ke));
}, G = (n) => !n || typeof n != "object" ? !1 : (typeof n.jquery < "u" && (n = n[0]), typeof n.nodeType < "u"), et = (n) => G(n) ? n.jquery ? n[0] : n : typeof n == "string" && n.length > 0 ? document.querySelector(Ss(n)) : null, Pt = (n) => {
  if (!G(n) || n.getClientRects().length === 0)
    return !1;
  const t = getComputedStyle(n).getPropertyValue("visibility") === "visible", e = n.closest("details:not([open])");
  if (!e)
    return t;
  if (e !== n) {
    const s = n.closest("summary");
    if (s && s.parentNode !== e || s === null)
      return !1;
  }
  return t;
}, nt = (n) => !n || n.nodeType !== Node.ELEMENT_NODE || n.classList.contains("disabled") ? !0 : typeof n.disabled < "u" ? n.disabled : n.hasAttribute("disabled") && n.getAttribute("disabled") !== "false", $s = (n) => {
  if (!document.documentElement.attachShadow)
    return null;
  if (typeof n.getRootNode == "function") {
    const t = n.getRootNode();
    return t instanceof ShadowRoot ? t : null;
  }
  return n instanceof ShadowRoot ? n : n.parentNode ? $s(n.parentNode) : null;
}, pe = () => {
}, Yt = (n) => {
  n.offsetHeight;
}, Ls = () => window.jQuery && !document.body.hasAttribute("data-bs-no-jquery") ? window.jQuery : null, Ne = [], er = (n) => {
  document.readyState === "loading" ? (Ne.length || document.addEventListener("DOMContentLoaded", () => {
    for (const t of Ne)
      t();
  }), Ne.push(n)) : n();
}, H = () => document.documentElement.dir === "rtl", j = (n) => {
  er(() => {
    const t = Ls();
    if (t) {
      const e = n.NAME, s = t.fn[e];
      t.fn[e] = n.jQueryInterface, t.fn[e].Constructor = n, t.fn[e].noConflict = () => (t.fn[e] = s, n.jQueryInterface);
    }
  });
}, x = (n, t = [], e = n) => typeof n == "function" ? n(...t) : e, Is = (n, t, e = !0) => {
  if (!e) {
    x(n);
    return;
  }
  const s = 5, i = tr(t) + s;
  let r = !1;
  const o = ({
    target: a
  }) => {
    a === t && (r = !0, t.removeEventListener(Ke, o), x(n));
  };
  t.addEventListener(Ke, o), setTimeout(() => {
    r || Ds(t);
  }, i);
}, cn = (n, t, e, s) => {
  const i = n.length;
  let r = n.indexOf(t);
  return r === -1 ? !e && s ? n[i - 1] : n[0] : (r += e ? 1 : -1, s && (r = (r + i) % i), n[Math.max(0, Math.min(r, i - 1))]);
}, nr = /[^.]*(?=\..*)\.|.*/, sr = /\..*/, ir = /::\d+$/, Se = {};
let Dn = 1;
const xs = {
  mouseenter: "mouseover",
  mouseleave: "mouseout"
}, rr = /* @__PURE__ */ new Set(["click", "dblclick", "mouseup", "mousedown", "contextmenu", "mousewheel", "DOMMouseScroll", "mouseover", "mouseout", "mousemove", "selectstart", "selectend", "keydown", "keypress", "keyup", "orientationchange", "touchstart", "touchmove", "touchend", "touchcancel", "pointerdown", "pointermove", "pointerup", "pointerleave", "pointercancel", "gesturestart", "gesturechange", "gestureend", "focus", "blur", "change", "reset", "select", "submit", "focusin", "focusout", "load", "unload", "beforeunload", "resize", "move", "DOMContentLoaded", "readystatechange", "error", "abort", "scroll"]);
function Rs(n, t) {
  return t && `${t}::${Dn++}` || n.uidEvent || Dn++;
}
function Ps(n) {
  const t = Rs(n);
  return n.uidEvent = t, Se[t] = Se[t] || {}, Se[t];
}
function or(n, t) {
  return function e(s) {
    return ln(s, {
      delegateTarget: n
    }), e.oneOff && l.off(n, s.type, t), t.apply(n, [s]);
  };
}
function ar(n, t, e) {
  return function s(i) {
    const r = n.querySelectorAll(t);
    for (let {
      target: o
    } = i; o && o !== this; o = o.parentNode)
      for (const a of r)
        if (a === o)
          return ln(i, {
            delegateTarget: o
          }), s.oneOff && l.off(n, i.type, t, e), e.apply(o, [i]);
  };
}
function Ms(n, t, e = null) {
  return Object.values(n).find((s) => s.callable === t && s.delegationSelector === e);
}
function ks(n, t, e) {
  const s = typeof t == "string", i = s ? e : t || e;
  let r = Vs(n);
  return rr.has(r) || (r = n), [s, i, r];
}
function $n(n, t, e, s, i) {
  if (typeof t != "string" || !n)
    return;
  let [r, o, a] = ks(t, e, s);
  t in xs && (o = ((A) => function(m) {
    if (!m.relatedTarget || m.relatedTarget !== m.delegateTarget && !m.delegateTarget.contains(m.relatedTarget))
      return A.call(this, m);
  })(o));
  const c = Ps(n), d = c[a] || (c[a] = {}), u = Ms(d, o, r ? e : null);
  if (u) {
    u.oneOff = u.oneOff && i;
    return;
  }
  const f = Rs(o, t.replace(nr, "")), _ = r ? ar(n, e, o) : or(n, o);
  _.delegationSelector = r ? e : null, _.callable = o, _.oneOff = i, _.uidEvent = f, d[f] = _, n.addEventListener(a, _, r);
}
function Ye(n, t, e, s, i) {
  const r = Ms(t[e], s, i);
  r && (n.removeEventListener(e, r, !!i), delete t[e][r.uidEvent]);
}
function cr(n, t, e, s) {
  const i = t[e] || {};
  for (const [r, o] of Object.entries(i))
    r.includes(s) && Ye(n, t, e, o.callable, o.delegationSelector);
}
function Vs(n) {
  return n = n.replace(sr, ""), xs[n] || n;
}
const l = {
  on(n, t, e, s) {
    $n(n, t, e, s, !1);
  },
  one(n, t, e, s) {
    $n(n, t, e, s, !0);
  },
  off(n, t, e, s) {
    if (typeof t != "string" || !n)
      return;
    const [i, r, o] = ks(t, e, s), a = o !== t, c = Ps(n), d = c[o] || {}, u = t.startsWith(".");
    if (typeof r < "u") {
      if (!Object.keys(d).length)
        return;
      Ye(n, c, o, r, i ? e : null);
      return;
    }
    if (u)
      for (const f of Object.keys(c))
        cr(n, c, f, t.slice(1));
    for (const [f, _] of Object.entries(d)) {
      const p = f.replace(ir, "");
      (!a || t.includes(p)) && Ye(n, c, o, _.callable, _.delegationSelector);
    }
  },
  trigger(n, t, e) {
    if (typeof t != "string" || !n)
      return null;
    const s = Ls(), i = Vs(t), r = t !== i;
    let o = null, a = !0, c = !0, d = !1;
    r && s && (o = s.Event(t, e), s(n).trigger(o), a = !o.isPropagationStopped(), c = !o.isImmediatePropagationStopped(), d = o.isDefaultPrevented());
    const u = ln(new Event(t, {
      bubbles: a,
      cancelable: !0
    }), e);
    return d && u.preventDefault(), c && n.dispatchEvent(u), u.defaultPrevented && o && o.preventDefault(), u;
  }
};
function ln(n, t = {}) {
  for (const [e, s] of Object.entries(t))
    try {
      n[e] = s;
    } catch {
      Object.defineProperty(n, e, {
        configurable: !0,
        get() {
          return s;
        }
      });
    }
  return n;
}
function Ln(n) {
  if (n === "true")
    return !0;
  if (n === "false")
    return !1;
  if (n === Number(n).toString())
    return Number(n);
  if (n === "" || n === "null")
    return null;
  if (typeof n != "string")
    return n;
  try {
    return JSON.parse(decodeURIComponent(n));
  } catch {
    return n;
  }
}
function De(n) {
  return n.replace(/[A-Z]/g, (t) => `-${t.toLowerCase()}`);
}
const q = {
  setDataAttribute(n, t, e) {
    n.setAttribute(`data-bs-${De(t)}`, e);
  },
  removeDataAttribute(n, t) {
    n.removeAttribute(`data-bs-${De(t)}`);
  },
  getDataAttributes(n) {
    if (!n)
      return {};
    const t = {}, e = Object.keys(n.dataset).filter((s) => s.startsWith("bs") && !s.startsWith("bsConfig"));
    for (const s of e) {
      let i = s.replace(/^bs/, "");
      i = i.charAt(0).toLowerCase() + i.slice(1, i.length), t[i] = Ln(n.dataset[s]);
    }
    return t;
  },
  getDataAttribute(n, t) {
    return Ln(n.getAttribute(`data-bs-${De(t)}`));
  }
};
class Ut {
  // Getters
  static get Default() {
    return {};
  }
  static get DefaultType() {
    return {};
  }
  static get NAME() {
    throw new Error('You have to implement the static method "NAME", for each component!');
  }
  _getConfig(t) {
    return t = this._mergeConfigObj(t), t = this._configAfterMerge(t), this._typeCheckConfig(t), t;
  }
  _configAfterMerge(t) {
    return t;
  }
  _mergeConfigObj(t, e) {
    const s = G(e) ? q.getDataAttribute(e, "config") : {};
    return {
      ...this.constructor.Default,
      ...typeof s == "object" ? s : {},
      ...G(e) ? q.getDataAttributes(e) : {},
      ...typeof t == "object" ? t : {}
    };
  }
  _typeCheckConfig(t, e = this.constructor.DefaultType) {
    for (const [s, i] of Object.entries(e)) {
      const r = t[s], o = G(r) ? "element" : Ji(r);
      if (!new RegExp(i).test(o))
        throw new TypeError(`${this.constructor.NAME.toUpperCase()}: Option "${s}" provided type "${o}" but expected type "${i}".`);
    }
  }
}
const lr = "5.3.3";
class Y extends Ut {
  constructor(t, e) {
    super(), t = et(t), t && (this._element = t, this._config = this._getConfig(e), Ce.set(this._element, this.constructor.DATA_KEY, this));
  }
  // Public
  dispose() {
    Ce.remove(this._element, this.constructor.DATA_KEY), l.off(this._element, this.constructor.EVENT_KEY);
    for (const t of Object.getOwnPropertyNames(this))
      this[t] = null;
  }
  _queueCallback(t, e, s = !0) {
    Is(t, e, s);
  }
  _getConfig(t) {
    return t = this._mergeConfigObj(t, this._element), t = this._configAfterMerge(t), this._typeCheckConfig(t), t;
  }
  // Static
  static getInstance(t) {
    return Ce.get(et(t), this.DATA_KEY);
  }
  static getOrCreateInstance(t, e = {}) {
    return this.getInstance(t) || new this(t, typeof e == "object" ? e : null);
  }
  static get VERSION() {
    return lr;
  }
  static get DATA_KEY() {
    return `bs.${this.NAME}`;
  }
  static get EVENT_KEY() {
    return `.${this.DATA_KEY}`;
  }
  static eventName(t) {
    return `${t}${this.EVENT_KEY}`;
  }
}
const $e = (n) => {
  let t = n.getAttribute("data-bs-target");
  if (!t || t === "#") {
    let e = n.getAttribute("href");
    if (!e || !e.includes("#") && !e.startsWith("."))
      return null;
    e.includes("#") && !e.startsWith("#") && (e = `#${e.split("#")[1]}`), t = e && e !== "#" ? e.trim() : null;
  }
  return t ? t.split(",").map((e) => Ss(e)).join(",") : null;
}, h = {
  find(n, t = document.documentElement) {
    return [].concat(...Element.prototype.querySelectorAll.call(t, n));
  },
  findOne(n, t = document.documentElement) {
    return Element.prototype.querySelector.call(t, n);
  },
  children(n, t) {
    return [].concat(...n.children).filter((e) => e.matches(t));
  },
  parents(n, t) {
    const e = [];
    let s = n.parentNode.closest(t);
    for (; s; )
      e.push(s), s = s.parentNode.closest(t);
    return e;
  },
  prev(n, t) {
    let e = n.previousElementSibling;
    for (; e; ) {
      if (e.matches(t))
        return [e];
      e = e.previousElementSibling;
    }
    return [];
  },
  // TODO: this is now unused; remove later along with prev()
  next(n, t) {
    let e = n.nextElementSibling;
    for (; e; ) {
      if (e.matches(t))
        return [e];
      e = e.nextElementSibling;
    }
    return [];
  },
  focusableChildren(n) {
    const t = ["a", "button", "input", "textarea", "select", "details", "[tabindex]", '[contenteditable="true"]'].map((e) => `${e}:not([tabindex^="-"])`).join(",");
    return this.find(t, n).filter((e) => !nt(e) && Pt(e));
  },
  getSelectorFromElement(n) {
    const t = $e(n);
    return t && h.findOne(t) ? t : null;
  },
  getElementFromSelector(n) {
    const t = $e(n);
    return t ? h.findOne(t) : null;
  },
  getMultipleElementsFromSelector(n) {
    const t = $e(n);
    return t ? h.find(t) : [];
  }
}, be = (n, t = "hide") => {
  const e = `click.dismiss${n.EVENT_KEY}`, s = n.NAME;
  l.on(document, e, `[data-bs-dismiss="${s}"]`, function(i) {
    if (["A", "AREA"].includes(this.tagName) && i.preventDefault(), nt(this))
      return;
    const r = h.getElementFromSelector(this) || this.closest(`.${s}`);
    n.getOrCreateInstance(r)[t]();
  });
}, ur = "alert", dr = "bs.alert", Hs = `.${dr}`, hr = `close${Hs}`, fr = `closed${Hs}`, pr = "fade", _r = "show";
class zt extends Y {
  // Getters
  static get NAME() {
    return ur;
  }
  // Public
  close() {
    if (l.trigger(this._element, hr).defaultPrevented)
      return;
    this._element.classList.remove(_r);
    const e = this._element.classList.contains(pr);
    this._queueCallback(() => this._destroyElement(), this._element, e);
  }
  // Private
  _destroyElement() {
    this._element.remove(), l.trigger(this._element, fr), this.dispose();
  }
  // Static
  static jQueryInterface(t) {
    return this.each(function() {
      const e = zt.getOrCreateInstance(this);
      if (typeof t == "string") {
        if (e[t] === void 0 || t.startsWith("_") || t === "constructor")
          throw new TypeError(`No method named "${t}"`);
        e[t](this);
      }
    });
  }
}
be(zt, "close");
j(zt);
const mr = "button", gr = "bs.button", Er = `.${gr}`, vr = ".data-api", br = "active", In = '[data-bs-toggle="button"]', Ar = `click${Er}${vr}`;
class Gt extends Y {
  // Getters
  static get NAME() {
    return mr;
  }
  // Public
  toggle() {
    this._element.setAttribute("aria-pressed", this._element.classList.toggle(br));
  }
  // Static
  static jQueryInterface(t) {
    return this.each(function() {
      const e = Gt.getOrCreateInstance(this);
      t === "toggle" && e[t]();
    });
  }
}
l.on(document, Ar, In, (n) => {
  n.preventDefault();
  const t = n.target.closest(In);
  Gt.getOrCreateInstance(t).toggle();
});
j(Gt);
const Tr = "swipe", Mt = ".bs.swipe", yr = `touchstart${Mt}`, wr = `touchmove${Mt}`, Or = `touchend${Mt}`, Cr = `pointerdown${Mt}`, Nr = `pointerup${Mt}`, Sr = "touch", Dr = "pen", $r = "pointer-event", Lr = 40, Ir = {
  endCallback: null,
  leftCallback: null,
  rightCallback: null
}, xr = {
  endCallback: "(function|null)",
  leftCallback: "(function|null)",
  rightCallback: "(function|null)"
};
class _e extends Ut {
  constructor(t, e) {
    super(), this._element = t, !(!t || !_e.isSupported()) && (this._config = this._getConfig(e), this._deltaX = 0, this._supportPointerEvents = !!window.PointerEvent, this._initEvents());
  }
  // Getters
  static get Default() {
    return Ir;
  }
  static get DefaultType() {
    return xr;
  }
  static get NAME() {
    return Tr;
  }
  // Public
  dispose() {
    l.off(this._element, Mt);
  }
  // Private
  _start(t) {
    if (!this._supportPointerEvents) {
      this._deltaX = t.touches[0].clientX;
      return;
    }
    this._eventIsPointerPenTouch(t) && (this._deltaX = t.clientX);
  }
  _end(t) {
    this._eventIsPointerPenTouch(t) && (this._deltaX = t.clientX - this._deltaX), this._handleSwipe(), x(this._config.endCallback);
  }
  _move(t) {
    this._deltaX = t.touches && t.touches.length > 1 ? 0 : t.touches[0].clientX - this._deltaX;
  }
  _handleSwipe() {
    const t = Math.abs(this._deltaX);
    if (t <= Lr)
      return;
    const e = t / this._deltaX;
    this._deltaX = 0, e && x(e > 0 ? this._config.rightCallback : this._config.leftCallback);
  }
  _initEvents() {
    this._supportPointerEvents ? (l.on(this._element, Cr, (t) => this._start(t)), l.on(this._element, Nr, (t) => this._end(t)), this._element.classList.add($r)) : (l.on(this._element, yr, (t) => this._start(t)), l.on(this._element, wr, (t) => this._move(t)), l.on(this._element, Or, (t) => this._end(t)));
  }
  _eventIsPointerPenTouch(t) {
    return this._supportPointerEvents && (t.pointerType === Dr || t.pointerType === Sr);
  }
  // Static
  static isSupported() {
    return "ontouchstart" in document.documentElement || navigator.maxTouchPoints > 0;
  }
}
const Rr = "carousel", Pr = "bs.carousel", rt = `.${Pr}`, Ws = ".data-api", Mr = "ArrowLeft", kr = "ArrowRight", Vr = 500, Wt = "next", Tt = "prev", Ot = "left", de = "right", Hr = `slide${rt}`, Le = `slid${rt}`, Wr = `keydown${rt}`, jr = `mouseenter${rt}`, Br = `mouseleave${rt}`, Fr = `dragstart${rt}`, Kr = `load${rt}${Ws}`, Yr = `click${rt}${Ws}`, js = "carousel", ie = "active", Ur = "slide", zr = "carousel-item-end", Gr = "carousel-item-start", qr = "carousel-item-next", Xr = "carousel-item-prev", Bs = ".active", Fs = ".carousel-item", Qr = Bs + Fs, Jr = ".carousel-item img", Zr = ".carousel-indicators", to = "[data-bs-slide], [data-bs-slide-to]", eo = '[data-bs-ride="carousel"]', no = {
  [Mr]: de,
  [kr]: Ot
}, so = {
  interval: 5e3,
  keyboard: !0,
  pause: "hover",
  ride: !1,
  touch: !0,
  wrap: !0
}, io = {
  interval: "(number|boolean)",
  // TODO:v6 remove boolean support
  keyboard: "boolean",
  pause: "(string|boolean)",
  ride: "(boolean|string)",
  touch: "boolean",
  wrap: "boolean"
};
class kt extends Y {
  constructor(t, e) {
    super(t, e), this._interval = null, this._activeElement = null, this._isSliding = !1, this.touchTimeout = null, this._swipeHelper = null, this._indicatorsElement = h.findOne(Zr, this._element), this._addEventListeners(), this._config.ride === js && this.cycle();
  }
  // Getters
  static get Default() {
    return so;
  }
  static get DefaultType() {
    return io;
  }
  static get NAME() {
    return Rr;
  }
  // Public
  next() {
    this._slide(Wt);
  }
  nextWhenVisible() {
    !document.hidden && Pt(this._element) && this.next();
  }
  prev() {
    this._slide(Tt);
  }
  pause() {
    this._isSliding && Ds(this._element), this._clearInterval();
  }
  cycle() {
    this._clearInterval(), this._updateInterval(), this._interval = setInterval(() => this.nextWhenVisible(), this._config.interval);
  }
  _maybeEnableCycle() {
    if (this._config.ride) {
      if (this._isSliding) {
        l.one(this._element, Le, () => this.cycle());
        return;
      }
      this.cycle();
    }
  }
  to(t) {
    const e = this._getItems();
    if (t > e.length - 1 || t < 0)
      return;
    if (this._isSliding) {
      l.one(this._element, Le, () => this.to(t));
      return;
    }
    const s = this._getItemIndex(this._getActive());
    if (s === t)
      return;
    const i = t > s ? Wt : Tt;
    this._slide(i, e[t]);
  }
  dispose() {
    this._swipeHelper && this._swipeHelper.dispose(), super.dispose();
  }
  // Private
  _configAfterMerge(t) {
    return t.defaultInterval = t.interval, t;
  }
  _addEventListeners() {
    this._config.keyboard && l.on(this._element, Wr, (t) => this._keydown(t)), this._config.pause === "hover" && (l.on(this._element, jr, () => this.pause()), l.on(this._element, Br, () => this._maybeEnableCycle())), this._config.touch && _e.isSupported() && this._addTouchEventListeners();
  }
  _addTouchEventListeners() {
    for (const s of h.find(Jr, this._element))
      l.on(s, Fr, (i) => i.preventDefault());
    const e = {
      leftCallback: () => this._slide(this._directionToOrder(Ot)),
      rightCallback: () => this._slide(this._directionToOrder(de)),
      endCallback: () => {
        this._config.pause === "hover" && (this.pause(), this.touchTimeout && clearTimeout(this.touchTimeout), this.touchTimeout = setTimeout(() => this._maybeEnableCycle(), Vr + this._config.interval));
      }
    };
    this._swipeHelper = new _e(this._element, e);
  }
  _keydown(t) {
    if (/input|textarea/i.test(t.target.tagName))
      return;
    const e = no[t.key];
    e && (t.preventDefault(), this._slide(this._directionToOrder(e)));
  }
  _getItemIndex(t) {
    return this._getItems().indexOf(t);
  }
  _setActiveIndicatorElement(t) {
    if (!this._indicatorsElement)
      return;
    const e = h.findOne(Bs, this._indicatorsElement);
    e.classList.remove(ie), e.removeAttribute("aria-current");
    const s = h.findOne(`[data-bs-slide-to="${t}"]`, this._indicatorsElement);
    s && (s.classList.add(ie), s.setAttribute("aria-current", "true"));
  }
  _updateInterval() {
    const t = this._activeElement || this._getActive();
    if (!t)
      return;
    const e = Number.parseInt(t.getAttribute("data-bs-interval"), 10);
    this._config.interval = e || this._config.defaultInterval;
  }
  _slide(t, e = null) {
    if (this._isSliding)
      return;
    const s = this._getActive(), i = t === Wt, r = e || cn(this._getItems(), s, i, this._config.wrap);
    if (r === s)
      return;
    const o = this._getItemIndex(r), a = (p) => l.trigger(this._element, p, {
      relatedTarget: r,
      direction: this._orderToDirection(t),
      from: this._getItemIndex(s),
      to: o
    });
    if (a(Hr).defaultPrevented || !s || !r)
      return;
    const d = !!this._interval;
    this.pause(), this._isSliding = !0, this._setActiveIndicatorElement(o), this._activeElement = r;
    const u = i ? Gr : zr, f = i ? qr : Xr;
    r.classList.add(f), Yt(r), s.classList.add(u), r.classList.add(u);
    const _ = () => {
      r.classList.remove(u, f), r.classList.add(ie), s.classList.remove(ie, f, u), this._isSliding = !1, a(Le);
    };
    this._queueCallback(_, s, this._isAnimated()), d && this.cycle();
  }
  _isAnimated() {
    return this._element.classList.contains(Ur);
  }
  _getActive() {
    return h.findOne(Qr, this._element);
  }
  _getItems() {
    return h.find(Fs, this._element);
  }
  _clearInterval() {
    this._interval && (clearInterval(this._interval), this._interval = null);
  }
  _directionToOrder(t) {
    return H() ? t === Ot ? Tt : Wt : t === Ot ? Wt : Tt;
  }
  _orderToDirection(t) {
    return H() ? t === Tt ? Ot : de : t === Tt ? de : Ot;
  }
  // Static
  static jQueryInterface(t) {
    return this.each(function() {
      const e = kt.getOrCreateInstance(this, t);
      if (typeof t == "number") {
        e.to(t);
        return;
      }
      if (typeof t == "string") {
        if (e[t] === void 0 || t.startsWith("_") || t === "constructor")
          throw new TypeError(`No method named "${t}"`);
        e[t]();
      }
    });
  }
}
l.on(document, Yr, to, function(n) {
  const t = h.getElementFromSelector(this);
  if (!t || !t.classList.contains(js))
    return;
  n.preventDefault();
  const e = kt.getOrCreateInstance(t), s = this.getAttribute("data-bs-slide-to");
  if (s) {
    e.to(s), e._maybeEnableCycle();
    return;
  }
  if (q.getDataAttribute(this, "slide") === "next") {
    e.next(), e._maybeEnableCycle();
    return;
  }
  e.prev(), e._maybeEnableCycle();
});
l.on(window, Kr, () => {
  const n = h.find(eo);
  for (const t of n)
    kt.getOrCreateInstance(t);
});
j(kt);
const ro = "collapse", oo = "bs.collapse", qt = `.${oo}`, ao = ".data-api", co = `show${qt}`, lo = `shown${qt}`, uo = `hide${qt}`, ho = `hidden${qt}`, fo = `click${qt}${ao}`, Ie = "show", Nt = "collapse", re = "collapsing", po = "collapsed", _o = `:scope .${Nt} .${Nt}`, mo = "collapse-horizontal", go = "width", Eo = "height", vo = ".collapse.show, .collapse.collapsing", Ue = '[data-bs-toggle="collapse"]', bo = {
  parent: null,
  toggle: !0
}, Ao = {
  parent: "(null|element)",
  toggle: "boolean"
};
class xt extends Y {
  constructor(t, e) {
    super(t, e), this._isTransitioning = !1, this._triggerArray = [];
    const s = h.find(Ue);
    for (const i of s) {
      const r = h.getSelectorFromElement(i), o = h.find(r).filter((a) => a === this._element);
      r !== null && o.length && this._triggerArray.push(i);
    }
    this._initializeChildren(), this._config.parent || this._addAriaAndCollapsedClass(this._triggerArray, this._isShown()), this._config.toggle && this.toggle();
  }
  // Getters
  static get Default() {
    return bo;
  }
  static get DefaultType() {
    return Ao;
  }
  static get NAME() {
    return ro;
  }
  // Public
  toggle() {
    this._isShown() ? this.hide() : this.show();
  }
  show() {
    if (this._isTransitioning || this._isShown())
      return;
    let t = [];
    if (this._config.parent && (t = this._getFirstLevelChildren(vo).filter((a) => a !== this._element).map((a) => xt.getOrCreateInstance(a, {
      toggle: !1
    }))), t.length && t[0]._isTransitioning || l.trigger(this._element, co).defaultPrevented)
      return;
    for (const a of t)
      a.hide();
    const s = this._getDimension();
    this._element.classList.remove(Nt), this._element.classList.add(re), this._element.style[s] = 0, this._addAriaAndCollapsedClass(this._triggerArray, !0), this._isTransitioning = !0;
    const i = () => {
      this._isTransitioning = !1, this._element.classList.remove(re), this._element.classList.add(Nt, Ie), this._element.style[s] = "", l.trigger(this._element, lo);
    }, o = `scroll${s[0].toUpperCase() + s.slice(1)}`;
    this._queueCallback(i, this._element, !0), this._element.style[s] = `${this._element[o]}px`;
  }
  hide() {
    if (this._isTransitioning || !this._isShown() || l.trigger(this._element, uo).defaultPrevented)
      return;
    const e = this._getDimension();
    this._element.style[e] = `${this._element.getBoundingClientRect()[e]}px`, Yt(this._element), this._element.classList.add(re), this._element.classList.remove(Nt, Ie);
    for (const i of this._triggerArray) {
      const r = h.getElementFromSelector(i);
      r && !this._isShown(r) && this._addAriaAndCollapsedClass([i], !1);
    }
    this._isTransitioning = !0;
    const s = () => {
      this._isTransitioning = !1, this._element.classList.remove(re), this._element.classList.add(Nt), l.trigger(this._element, ho);
    };
    this._element.style[e] = "", this._queueCallback(s, this._element, !0);
  }
  _isShown(t = this._element) {
    return t.classList.contains(Ie);
  }
  // Private
  _configAfterMerge(t) {
    return t.toggle = !!t.toggle, t.parent = et(t.parent), t;
  }
  _getDimension() {
    return this._element.classList.contains(mo) ? go : Eo;
  }
  _initializeChildren() {
    if (!this._config.parent)
      return;
    const t = this._getFirstLevelChildren(Ue);
    for (const e of t) {
      const s = h.getElementFromSelector(e);
      s && this._addAriaAndCollapsedClass([e], this._isShown(s));
    }
  }
  _getFirstLevelChildren(t) {
    const e = h.find(_o, this._config.parent);
    return h.find(t, this._config.parent).filter((s) => !e.includes(s));
  }
  _addAriaAndCollapsedClass(t, e) {
    if (t.length)
      for (const s of t)
        s.classList.toggle(po, !e), s.setAttribute("aria-expanded", e);
  }
  // Static
  static jQueryInterface(t) {
    const e = {};
    return typeof t == "string" && /show|hide/.test(t) && (e.toggle = !1), this.each(function() {
      const s = xt.getOrCreateInstance(this, e);
      if (typeof t == "string") {
        if (typeof s[t] > "u")
          throw new TypeError(`No method named "${t}"`);
        s[t]();
      }
    });
  }
}
l.on(document, fo, Ue, function(n) {
  (n.target.tagName === "A" || n.delegateTarget && n.delegateTarget.tagName === "A") && n.preventDefault();
  for (const t of h.getMultipleElementsFromSelector(this))
    xt.getOrCreateInstance(t, {
      toggle: !1
    }).toggle();
});
j(xt);
const xn = "dropdown", To = "bs.dropdown", Et = `.${To}`, un = ".data-api", yo = "Escape", Rn = "Tab", wo = "ArrowUp", Pn = "ArrowDown", Oo = 2, Co = `hide${Et}`, No = `hidden${Et}`, So = `show${Et}`, Do = `shown${Et}`, Ks = `click${Et}${un}`, Ys = `keydown${Et}${un}`, $o = `keyup${Et}${un}`, Ct = "show", Lo = "dropup", Io = "dropend", xo = "dropstart", Ro = "dropup-center", Po = "dropdown-center", ht = '[data-bs-toggle="dropdown"]:not(.disabled):not(:disabled)', Mo = `${ht}.${Ct}`, he = ".dropdown-menu", ko = ".navbar", Vo = ".navbar-nav", Ho = ".dropdown-menu .dropdown-item:not(.disabled):not(:disabled)", Wo = H() ? "top-end" : "top-start", jo = H() ? "top-start" : "top-end", Bo = H() ? "bottom-end" : "bottom-start", Fo = H() ? "bottom-start" : "bottom-end", Ko = H() ? "left-start" : "right-start", Yo = H() ? "right-start" : "left-start", Uo = "top", zo = "bottom", Go = {
  autoClose: !0,
  boundary: "clippingParents",
  display: "dynamic",
  offset: [0, 2],
  popperConfig: null,
  reference: "toggle"
}, qo = {
  autoClose: "(boolean|string)",
  boundary: "(string|element)",
  display: "string",
  offset: "(array|string|function)",
  popperConfig: "(null|object|function)",
  reference: "(string|element|object)"
};
class K extends Y {
  constructor(t, e) {
    super(t, e), this._popper = null, this._parent = this._element.parentNode, this._menu = h.next(this._element, he)[0] || h.prev(this._element, he)[0] || h.findOne(he, this._parent), this._inNavbar = this._detectNavbar();
  }
  // Getters
  static get Default() {
    return Go;
  }
  static get DefaultType() {
    return qo;
  }
  static get NAME() {
    return xn;
  }
  // Public
  toggle() {
    return this._isShown() ? this.hide() : this.show();
  }
  show() {
    if (nt(this._element) || this._isShown())
      return;
    const t = {
      relatedTarget: this._element
    };
    if (!l.trigger(this._element, So, t).defaultPrevented) {
      if (this._createPopper(), "ontouchstart" in document.documentElement && !this._parent.closest(Vo))
        for (const s of [].concat(...document.body.children))
          l.on(s, "mouseover", pe);
      this._element.focus(), this._element.setAttribute("aria-expanded", !0), this._menu.classList.add(Ct), this._element.classList.add(Ct), l.trigger(this._element, Do, t);
    }
  }
  hide() {
    if (nt(this._element) || !this._isShown())
      return;
    const t = {
      relatedTarget: this._element
    };
    this._completeHide(t);
  }
  dispose() {
    this._popper && this._popper.destroy(), super.dispose();
  }
  update() {
    this._inNavbar = this._detectNavbar(), this._popper && this._popper.update();
  }
  // Private
  _completeHide(t) {
    if (!l.trigger(this._element, Co, t).defaultPrevented) {
      if ("ontouchstart" in document.documentElement)
        for (const s of [].concat(...document.body.children))
          l.off(s, "mouseover", pe);
      this._popper && this._popper.destroy(), this._menu.classList.remove(Ct), this._element.classList.remove(Ct), this._element.setAttribute("aria-expanded", "false"), q.removeDataAttribute(this._menu, "popper"), l.trigger(this._element, No, t);
    }
  }
  _getConfig(t) {
    if (t = super._getConfig(t), typeof t.reference == "object" && !G(t.reference) && typeof t.reference.getBoundingClientRect != "function")
      throw new TypeError(`${xn.toUpperCase()}: Option "reference" provided type "object" without a required "getBoundingClientRect" method.`);
    return t;
  }
  _createPopper() {
    if (typeof Ns > "u")
      throw new TypeError("Bootstrap's dropdowns require Popper (https://popper.js.org)");
    let t = this._element;
    this._config.reference === "parent" ? t = this._parent : G(this._config.reference) ? t = et(this._config.reference) : typeof this._config.reference == "object" && (t = this._config.reference);
    const e = this._getPopperConfig();
    this._popper = an(t, this._menu, e);
  }
  _isShown() {
    return this._menu.classList.contains(Ct);
  }
  _getPlacement() {
    const t = this._parent;
    if (t.classList.contains(Io))
      return Ko;
    if (t.classList.contains(xo))
      return Yo;
    if (t.classList.contains(Ro))
      return Uo;
    if (t.classList.contains(Po))
      return zo;
    const e = getComputedStyle(this._menu).getPropertyValue("--bs-position").trim() === "end";
    return t.classList.contains(Lo) ? e ? jo : Wo : e ? Fo : Bo;
  }
  _detectNavbar() {
    return this._element.closest(ko) !== null;
  }
  _getOffset() {
    const {
      offset: t
    } = this._config;
    return typeof t == "string" ? t.split(",").map((e) => Number.parseInt(e, 10)) : typeof t == "function" ? (e) => t(e, this._element) : t;
  }
  _getPopperConfig() {
    const t = {
      placement: this._getPlacement(),
      modifiers: [{
        name: "preventOverflow",
        options: {
          boundary: this._config.boundary
        }
      }, {
        name: "offset",
        options: {
          offset: this._getOffset()
        }
      }]
    };
    return (this._inNavbar || this._config.display === "static") && (q.setDataAttribute(this._menu, "popper", "static"), t.modifiers = [{
      name: "applyStyles",
      enabled: !1
    }]), {
      ...t,
      ...x(this._config.popperConfig, [t])
    };
  }
  _selectMenuItem({
    key: t,
    target: e
  }) {
    const s = h.find(Ho, this._menu).filter((i) => Pt(i));
    s.length && cn(s, e, t === Pn, !s.includes(e)).focus();
  }
  // Static
  static jQueryInterface(t) {
    return this.each(function() {
      const e = K.getOrCreateInstance(this, t);
      if (typeof t == "string") {
        if (typeof e[t] > "u")
          throw new TypeError(`No method named "${t}"`);
        e[t]();
      }
    });
  }
  static clearMenus(t) {
    if (t.button === Oo || t.type === "keyup" && t.key !== Rn)
      return;
    const e = h.find(Mo);
    for (const s of e) {
      const i = K.getInstance(s);
      if (!i || i._config.autoClose === !1)
        continue;
      const r = t.composedPath(), o = r.includes(i._menu);
      if (r.includes(i._element) || i._config.autoClose === "inside" && !o || i._config.autoClose === "outside" && o || i._menu.contains(t.target) && (t.type === "keyup" && t.key === Rn || /input|select|option|textarea|form/i.test(t.target.tagName)))
        continue;
      const a = {
        relatedTarget: i._element
      };
      t.type === "click" && (a.clickEvent = t), i._completeHide(a);
    }
  }
  static dataApiKeydownHandler(t) {
    const e = /input|textarea/i.test(t.target.tagName), s = t.key === yo, i = [wo, Pn].includes(t.key);
    if (!i && !s || e && !s)
      return;
    t.preventDefault();
    const r = this.matches(ht) ? this : h.prev(this, ht)[0] || h.next(this, ht)[0] || h.findOne(ht, t.delegateTarget.parentNode), o = K.getOrCreateInstance(r);
    if (i) {
      t.stopPropagation(), o.show(), o._selectMenuItem(t);
      return;
    }
    o._isShown() && (t.stopPropagation(), o.hide(), r.focus());
  }
}
l.on(document, Ys, ht, K.dataApiKeydownHandler);
l.on(document, Ys, he, K.dataApiKeydownHandler);
l.on(document, Ks, K.clearMenus);
l.on(document, $o, K.clearMenus);
l.on(document, Ks, ht, function(n) {
  n.preventDefault(), K.getOrCreateInstance(this).toggle();
});
j(K);
const Us = "backdrop", Xo = "fade", Mn = "show", kn = `mousedown.bs.${Us}`, Qo = {
  className: "modal-backdrop",
  clickCallback: null,
  isAnimated: !1,
  isVisible: !0,
  // if false, we use the backdrop helper without adding any element to the dom
  rootElement: "body"
  // give the choice to place backdrop under different elements
}, Jo = {
  className: "string",
  clickCallback: "(function|null)",
  isAnimated: "boolean",
  isVisible: "boolean",
  rootElement: "(element|string)"
};
class zs extends Ut {
  constructor(t) {
    super(), this._config = this._getConfig(t), this._isAppended = !1, this._element = null;
  }
  // Getters
  static get Default() {
    return Qo;
  }
  static get DefaultType() {
    return Jo;
  }
  static get NAME() {
    return Us;
  }
  // Public
  show(t) {
    if (!this._config.isVisible) {
      x(t);
      return;
    }
    this._append();
    const e = this._getElement();
    this._config.isAnimated && Yt(e), e.classList.add(Mn), this._emulateAnimation(() => {
      x(t);
    });
  }
  hide(t) {
    if (!this._config.isVisible) {
      x(t);
      return;
    }
    this._getElement().classList.remove(Mn), this._emulateAnimation(() => {
      this.dispose(), x(t);
    });
  }
  dispose() {
    this._isAppended && (l.off(this._element, kn), this._element.remove(), this._isAppended = !1);
  }
  // Private
  _getElement() {
    if (!this._element) {
      const t = document.createElement("div");
      t.className = this._config.className, this._config.isAnimated && t.classList.add(Xo), this._element = t;
    }
    return this._element;
  }
  _configAfterMerge(t) {
    return t.rootElement = et(t.rootElement), t;
  }
  _append() {
    if (this._isAppended)
      return;
    const t = this._getElement();
    this._config.rootElement.append(t), l.on(t, kn, () => {
      x(this._config.clickCallback);
    }), this._isAppended = !0;
  }
  _emulateAnimation(t) {
    Is(t, this._getElement(), this._config.isAnimated);
  }
}
const Zo = "focustrap", ta = "bs.focustrap", me = `.${ta}`, ea = `focusin${me}`, na = `keydown.tab${me}`, sa = "Tab", ia = "forward", Vn = "backward", ra = {
  autofocus: !0,
  trapElement: null
  // The element to trap focus inside of
}, oa = {
  autofocus: "boolean",
  trapElement: "element"
};
class Gs extends Ut {
  constructor(t) {
    super(), this._config = this._getConfig(t), this._isActive = !1, this._lastTabNavDirection = null;
  }
  // Getters
  static get Default() {
    return ra;
  }
  static get DefaultType() {
    return oa;
  }
  static get NAME() {
    return Zo;
  }
  // Public
  activate() {
    this._isActive || (this._config.autofocus && this._config.trapElement.focus(), l.off(document, me), l.on(document, ea, (t) => this._handleFocusin(t)), l.on(document, na, (t) => this._handleKeydown(t)), this._isActive = !0);
  }
  deactivate() {
    this._isActive && (this._isActive = !1, l.off(document, me));
  }
  // Private
  _handleFocusin(t) {
    const {
      trapElement: e
    } = this._config;
    if (t.target === document || t.target === e || e.contains(t.target))
      return;
    const s = h.focusableChildren(e);
    s.length === 0 ? e.focus() : this._lastTabNavDirection === Vn ? s[s.length - 1].focus() : s[0].focus();
  }
  _handleKeydown(t) {
    t.key === sa && (this._lastTabNavDirection = t.shiftKey ? Vn : ia);
  }
}
const Hn = ".fixed-top, .fixed-bottom, .is-fixed, .sticky-top", Wn = ".sticky-top", oe = "padding-right", jn = "margin-right";
class ze {
  constructor() {
    this._element = document.body;
  }
  // Public
  getWidth() {
    const t = document.documentElement.clientWidth;
    return Math.abs(window.innerWidth - t);
  }
  hide() {
    const t = this.getWidth();
    this._disableOverFlow(), this._setElementAttributes(this._element, oe, (e) => e + t), this._setElementAttributes(Hn, oe, (e) => e + t), this._setElementAttributes(Wn, jn, (e) => e - t);
  }
  reset() {
    this._resetElementAttributes(this._element, "overflow"), this._resetElementAttributes(this._element, oe), this._resetElementAttributes(Hn, oe), this._resetElementAttributes(Wn, jn);
  }
  isOverflowing() {
    return this.getWidth() > 0;
  }
  // Private
  _disableOverFlow() {
    this._saveInitialAttribute(this._element, "overflow"), this._element.style.overflow = "hidden";
  }
  _setElementAttributes(t, e, s) {
    const i = this.getWidth(), r = (o) => {
      if (o !== this._element && window.innerWidth > o.clientWidth + i)
        return;
      this._saveInitialAttribute(o, e);
      const a = window.getComputedStyle(o).getPropertyValue(e);
      o.style.setProperty(e, `${s(Number.parseFloat(a))}px`);
    };
    this._applyManipulationCallback(t, r);
  }
  _saveInitialAttribute(t, e) {
    const s = t.style.getPropertyValue(e);
    s && q.setDataAttribute(t, e, s);
  }
  _resetElementAttributes(t, e) {
    const s = (i) => {
      const r = q.getDataAttribute(i, e);
      if (r === null) {
        i.style.removeProperty(e);
        return;
      }
      q.removeDataAttribute(i, e), i.style.setProperty(e, r);
    };
    this._applyManipulationCallback(t, s);
  }
  _applyManipulationCallback(t, e) {
    if (G(t)) {
      e(t);
      return;
    }
    for (const s of h.find(t, this._element))
      e(s);
  }
}
const aa = "modal", ca = "bs.modal", W = `.${ca}`, la = ".data-api", ua = "Escape", da = `hide${W}`, ha = `hidePrevented${W}`, qs = `hidden${W}`, Xs = `show${W}`, fa = `shown${W}`, pa = `resize${W}`, _a = `click.dismiss${W}`, ma = `mousedown.dismiss${W}`, ga = `keydown.dismiss${W}`, Ea = `click${W}${la}`, Bn = "modal-open", va = "fade", Fn = "show", xe = "modal-static", ba = ".modal.show", Aa = ".modal-dialog", Ta = ".modal-body", ya = '[data-bs-toggle="modal"]', wa = {
  backdrop: !0,
  focus: !0,
  keyboard: !0
}, Oa = {
  backdrop: "(boolean|string)",
  focus: "boolean",
  keyboard: "boolean"
};
class gt extends Y {
  constructor(t, e) {
    super(t, e), this._dialog = h.findOne(Aa, this._element), this._backdrop = this._initializeBackDrop(), this._focustrap = this._initializeFocusTrap(), this._isShown = !1, this._isTransitioning = !1, this._scrollBar = new ze(), this._addEventListeners();
  }
  // Getters
  static get Default() {
    return wa;
  }
  static get DefaultType() {
    return Oa;
  }
  static get NAME() {
    return aa;
  }
  // Public
  toggle(t) {
    return this._isShown ? this.hide() : this.show(t);
  }
  show(t) {
    this._isShown || this._isTransitioning || l.trigger(this._element, Xs, {
      relatedTarget: t
    }).defaultPrevented || (this._isShown = !0, this._isTransitioning = !0, this._scrollBar.hide(), document.body.classList.add(Bn), this._adjustDialog(), this._backdrop.show(() => this._showElement(t)));
  }
  hide() {
    !this._isShown || this._isTransitioning || l.trigger(this._element, da).defaultPrevented || (this._isShown = !1, this._isTransitioning = !0, this._focustrap.deactivate(), this._element.classList.remove(Fn), this._queueCallback(() => this._hideModal(), this._element, this._isAnimated()));
  }
  dispose() {
    l.off(window, W), l.off(this._dialog, W), this._backdrop.dispose(), this._focustrap.deactivate(), super.dispose();
  }
  handleUpdate() {
    this._adjustDialog();
  }
  // Private
  _initializeBackDrop() {
    return new zs({
      isVisible: !!this._config.backdrop,
      // 'static' option will be translated to true, and booleans will keep their value,
      isAnimated: this._isAnimated()
    });
  }
  _initializeFocusTrap() {
    return new Gs({
      trapElement: this._element
    });
  }
  _showElement(t) {
    document.body.contains(this._element) || document.body.append(this._element), this._element.style.display = "block", this._element.removeAttribute("aria-hidden"), this._element.setAttribute("aria-modal", !0), this._element.setAttribute("role", "dialog"), this._element.scrollTop = 0;
    const e = h.findOne(Ta, this._dialog);
    e && (e.scrollTop = 0), Yt(this._element), this._element.classList.add(Fn);
    const s = () => {
      this._config.focus && this._focustrap.activate(), this._isTransitioning = !1, l.trigger(this._element, fa, {
        relatedTarget: t
      });
    };
    this._queueCallback(s, this._dialog, this._isAnimated());
  }
  _addEventListeners() {
    l.on(this._element, ga, (t) => {
      if (t.key === ua) {
        if (this._config.keyboard) {
          this.hide();
          return;
        }
        this._triggerBackdropTransition();
      }
    }), l.on(window, pa, () => {
      this._isShown && !this._isTransitioning && this._adjustDialog();
    }), l.on(this._element, ma, (t) => {
      l.one(this._element, _a, (e) => {
        if (!(this._element !== t.target || this._element !== e.target)) {
          if (this._config.backdrop === "static") {
            this._triggerBackdropTransition();
            return;
          }
          this._config.backdrop && this.hide();
        }
      });
    });
  }
  _hideModal() {
    this._element.style.display = "none", this._element.setAttribute("aria-hidden", !0), this._element.removeAttribute("aria-modal"), this._element.removeAttribute("role"), this._isTransitioning = !1, this._backdrop.hide(() => {
      document.body.classList.remove(Bn), this._resetAdjustments(), this._scrollBar.reset(), l.trigger(this._element, qs);
    });
  }
  _isAnimated() {
    return this._element.classList.contains(va);
  }
  _triggerBackdropTransition() {
    if (l.trigger(this._element, ha).defaultPrevented)
      return;
    const e = this._element.scrollHeight > document.documentElement.clientHeight, s = this._element.style.overflowY;
    s === "hidden" || this._element.classList.contains(xe) || (e || (this._element.style.overflowY = "hidden"), this._element.classList.add(xe), this._queueCallback(() => {
      this._element.classList.remove(xe), this._queueCallback(() => {
        this._element.style.overflowY = s;
      }, this._dialog);
    }, this._dialog), this._element.focus());
  }
  /**
   * The following methods are used to handle overflowing modals
   */
  _adjustDialog() {
    const t = this._element.scrollHeight > document.documentElement.clientHeight, e = this._scrollBar.getWidth(), s = e > 0;
    if (s && !t) {
      const i = H() ? "paddingLeft" : "paddingRight";
      this._element.style[i] = `${e}px`;
    }
    if (!s && t) {
      const i = H() ? "paddingRight" : "paddingLeft";
      this._element.style[i] = `${e}px`;
    }
  }
  _resetAdjustments() {
    this._element.style.paddingLeft = "", this._element.style.paddingRight = "";
  }
  // Static
  static jQueryInterface(t, e) {
    return this.each(function() {
      const s = gt.getOrCreateInstance(this, t);
      if (typeof t == "string") {
        if (typeof s[t] > "u")
          throw new TypeError(`No method named "${t}"`);
        s[t](e);
      }
    });
  }
}
l.on(document, Ea, ya, function(n) {
  const t = h.getElementFromSelector(this);
  ["A", "AREA"].includes(this.tagName) && n.preventDefault(), l.one(t, Xs, (i) => {
    i.defaultPrevented || l.one(t, qs, () => {
      Pt(this) && this.focus();
    });
  });
  const e = h.findOne(ba);
  e && gt.getInstance(e).hide(), gt.getOrCreateInstance(t).toggle(this);
});
be(gt);
j(gt);
const Ca = "offcanvas", Na = "bs.offcanvas", J = `.${Na}`, Qs = ".data-api", Sa = `load${J}${Qs}`, Da = "Escape", Kn = "show", Yn = "showing", Un = "hiding", $a = "offcanvas-backdrop", Js = ".offcanvas.show", La = `show${J}`, Ia = `shown${J}`, xa = `hide${J}`, zn = `hidePrevented${J}`, Zs = `hidden${J}`, Ra = `resize${J}`, Pa = `click${J}${Qs}`, Ma = `keydown.dismiss${J}`, ka = '[data-bs-toggle="offcanvas"]', Va = {
  backdrop: !0,
  keyboard: !0,
  scroll: !1
}, Ha = {
  backdrop: "(boolean|string)",
  keyboard: "boolean",
  scroll: "boolean"
};
class Q extends Y {
  constructor(t, e) {
    super(t, e), this._isShown = !1, this._backdrop = this._initializeBackDrop(), this._focustrap = this._initializeFocusTrap(), this._addEventListeners();
  }
  // Getters
  static get Default() {
    return Va;
  }
  static get DefaultType() {
    return Ha;
  }
  static get NAME() {
    return Ca;
  }
  // Public
  toggle(t) {
    return this._isShown ? this.hide() : this.show(t);
  }
  show(t) {
    if (this._isShown || l.trigger(this._element, La, {
      relatedTarget: t
    }).defaultPrevented)
      return;
    this._isShown = !0, this._backdrop.show(), this._config.scroll || new ze().hide(), this._element.setAttribute("aria-modal", !0), this._element.setAttribute("role", "dialog"), this._element.classList.add(Yn);
    const s = () => {
      (!this._config.scroll || this._config.backdrop) && this._focustrap.activate(), this._element.classList.add(Kn), this._element.classList.remove(Yn), l.trigger(this._element, Ia, {
        relatedTarget: t
      });
    };
    this._queueCallback(s, this._element, !0);
  }
  hide() {
    if (!this._isShown || l.trigger(this._element, xa).defaultPrevented)
      return;
    this._focustrap.deactivate(), this._element.blur(), this._isShown = !1, this._element.classList.add(Un), this._backdrop.hide();
    const e = () => {
      this._element.classList.remove(Kn, Un), this._element.removeAttribute("aria-modal"), this._element.removeAttribute("role"), this._config.scroll || new ze().reset(), l.trigger(this._element, Zs);
    };
    this._queueCallback(e, this._element, !0);
  }
  dispose() {
    this._backdrop.dispose(), this._focustrap.deactivate(), super.dispose();
  }
  // Private
  _initializeBackDrop() {
    const t = () => {
      if (this._config.backdrop === "static") {
        l.trigger(this._element, zn);
        return;
      }
      this.hide();
    }, e = !!this._config.backdrop;
    return new zs({
      className: $a,
      isVisible: e,
      isAnimated: !0,
      rootElement: this._element.parentNode,
      clickCallback: e ? t : null
    });
  }
  _initializeFocusTrap() {
    return new Gs({
      trapElement: this._element
    });
  }
  _addEventListeners() {
    l.on(this._element, Ma, (t) => {
      if (t.key === Da) {
        if (this._config.keyboard) {
          this.hide();
          return;
        }
        l.trigger(this._element, zn);
      }
    });
  }
  // Static
  static jQueryInterface(t) {
    return this.each(function() {
      const e = Q.getOrCreateInstance(this, t);
      if (typeof t == "string") {
        if (e[t] === void 0 || t.startsWith("_") || t === "constructor")
          throw new TypeError(`No method named "${t}"`);
        e[t](this);
      }
    });
  }
}
l.on(document, Pa, ka, function(n) {
  const t = h.getElementFromSelector(this);
  if (["A", "AREA"].includes(this.tagName) && n.preventDefault(), nt(this))
    return;
  l.one(t, Zs, () => {
    Pt(this) && this.focus();
  });
  const e = h.findOne(Js);
  e && e !== t && Q.getInstance(e).hide(), Q.getOrCreateInstance(t).toggle(this);
});
l.on(window, Sa, () => {
  for (const n of h.find(Js))
    Q.getOrCreateInstance(n).show();
});
l.on(window, Ra, () => {
  for (const n of h.find("[aria-modal][class*=show][class*=offcanvas-]"))
    getComputedStyle(n).position !== "fixed" && Q.getOrCreateInstance(n).hide();
});
be(Q);
j(Q);
const Wa = /^aria-[\w-]*$/i, ti = {
  // Global attributes allowed on any supplied element below.
  "*": ["class", "dir", "id", "lang", "role", Wa],
  a: ["target", "href", "title", "rel"],
  area: [],
  b: [],
  br: [],
  col: [],
  code: [],
  dd: [],
  div: [],
  dl: [],
  dt: [],
  em: [],
  hr: [],
  h1: [],
  h2: [],
  h3: [],
  h4: [],
  h5: [],
  h6: [],
  i: [],
  img: ["src", "srcset", "alt", "title", "width", "height"],
  li: [],
  ol: [],
  p: [],
  pre: [],
  s: [],
  small: [],
  span: [],
  sub: [],
  sup: [],
  strong: [],
  u: [],
  ul: []
}, ja = /* @__PURE__ */ new Set(["background", "cite", "href", "itemtype", "longdesc", "poster", "src", "xlink:href"]), Ba = /^(?!javascript:)(?:[a-z0-9+.-]+:|[^&:/?#]*(?:[/?#]|$))/i, Fa = (n, t) => {
  const e = n.nodeName.toLowerCase();
  return t.includes(e) ? ja.has(e) ? !!Ba.test(n.nodeValue) : !0 : t.filter((s) => s instanceof RegExp).some((s) => s.test(e));
};
function Ka(n, t, e) {
  if (!n.length)
    return n;
  if (e && typeof e == "function")
    return e(n);
  const i = new window.DOMParser().parseFromString(n, "text/html"), r = [].concat(...i.body.querySelectorAll("*"));
  for (const o of r) {
    const a = o.nodeName.toLowerCase();
    if (!Object.keys(t).includes(a)) {
      o.remove();
      continue;
    }
    const c = [].concat(...o.attributes), d = [].concat(t["*"] || [], t[a] || []);
    for (const u of c)
      Fa(u, d) || o.removeAttribute(u.nodeName);
  }
  return i.body.innerHTML;
}
const Ya = "TemplateFactory", Ua = {
  allowList: ti,
  content: {},
  // { selector : text ,  selector2 : text2 , }
  extraClass: "",
  html: !1,
  sanitize: !0,
  sanitizeFn: null,
  template: "<div></div>"
}, za = {
  allowList: "object",
  content: "object",
  extraClass: "(string|function)",
  html: "boolean",
  sanitize: "boolean",
  sanitizeFn: "(null|function)",
  template: "string"
}, Ga = {
  entry: "(string|element|function|null)",
  selector: "(string|element)"
};
class qa extends Ut {
  constructor(t) {
    super(), this._config = this._getConfig(t);
  }
  // Getters
  static get Default() {
    return Ua;
  }
  static get DefaultType() {
    return za;
  }
  static get NAME() {
    return Ya;
  }
  // Public
  getContent() {
    return Object.values(this._config.content).map((t) => this._resolvePossibleFunction(t)).filter(Boolean);
  }
  hasContent() {
    return this.getContent().length > 0;
  }
  changeContent(t) {
    return this._checkContent(t), this._config.content = {
      ...this._config.content,
      ...t
    }, this;
  }
  toHtml() {
    const t = document.createElement("div");
    t.innerHTML = this._maybeSanitize(this._config.template);
    for (const [i, r] of Object.entries(this._config.content))
      this._setContent(t, r, i);
    const e = t.children[0], s = this._resolvePossibleFunction(this._config.extraClass);
    return s && e.classList.add(...s.split(" ")), e;
  }
  // Private
  _typeCheckConfig(t) {
    super._typeCheckConfig(t), this._checkContent(t.content);
  }
  _checkContent(t) {
    for (const [e, s] of Object.entries(t))
      super._typeCheckConfig({
        selector: e,
        entry: s
      }, Ga);
  }
  _setContent(t, e, s) {
    const i = h.findOne(s, t);
    if (i) {
      if (e = this._resolvePossibleFunction(e), !e) {
        i.remove();
        return;
      }
      if (G(e)) {
        this._putElementInTemplate(et(e), i);
        return;
      }
      if (this._config.html) {
        i.innerHTML = this._maybeSanitize(e);
        return;
      }
      i.textContent = e;
    }
  }
  _maybeSanitize(t) {
    return this._config.sanitize ? Ka(t, this._config.allowList, this._config.sanitizeFn) : t;
  }
  _resolvePossibleFunction(t) {
    return x(t, [this]);
  }
  _putElementInTemplate(t, e) {
    if (this._config.html) {
      e.innerHTML = "", e.append(t);
      return;
    }
    e.textContent = t.textContent;
  }
}
const Xa = "tooltip", Qa = /* @__PURE__ */ new Set(["sanitize", "allowList", "sanitizeFn"]), Re = "fade", Ja = "modal", ae = "show", Za = ".tooltip-inner", Gn = `.${Ja}`, qn = "hide.bs.modal", jt = "hover", Pe = "focus", tc = "click", ec = "manual", nc = "hide", sc = "hidden", ic = "show", rc = "shown", oc = "inserted", ac = "click", cc = "focusin", lc = "focusout", uc = "mouseenter", dc = "mouseleave", hc = {
  AUTO: "auto",
  TOP: "top",
  RIGHT: H() ? "left" : "right",
  BOTTOM: "bottom",
  LEFT: H() ? "right" : "left"
}, fc = {
  allowList: ti,
  animation: !0,
  boundary: "clippingParents",
  container: !1,
  customClass: "",
  delay: 0,
  fallbackPlacements: ["top", "right", "bottom", "left"],
  html: !1,
  offset: [0, 6],
  placement: "top",
  popperConfig: null,
  sanitize: !0,
  sanitizeFn: null,
  selector: !1,
  template: '<div class="tooltip" role="tooltip"><div class="tooltip-arrow"></div><div class="tooltip-inner"></div></div>',
  title: "",
  trigger: "hover focus"
}, pc = {
  allowList: "object",
  animation: "boolean",
  boundary: "(string|element)",
  container: "(string|element|boolean)",
  customClass: "(string|function)",
  delay: "(number|object)",
  fallbackPlacements: "array",
  html: "boolean",
  offset: "(array|string|function)",
  placement: "(string|function)",
  popperConfig: "(null|object|function)",
  sanitize: "boolean",
  sanitizeFn: "(null|function)",
  selector: "(string|boolean)",
  template: "string",
  title: "(string|element|function)",
  trigger: "string"
};
class vt extends Y {
  constructor(t, e) {
    if (typeof Ns > "u")
      throw new TypeError("Bootstrap's tooltips require Popper (https://popper.js.org)");
    super(t, e), this._isEnabled = !0, this._timeout = 0, this._isHovered = null, this._activeTrigger = {}, this._popper = null, this._templateFactory = null, this._newContent = null, this.tip = null, this._setListeners(), this._config.selector || this._fixTitle();
  }
  // Getters
  static get Default() {
    return fc;
  }
  static get DefaultType() {
    return pc;
  }
  static get NAME() {
    return Xa;
  }
  // Public
  enable() {
    this._isEnabled = !0;
  }
  disable() {
    this._isEnabled = !1;
  }
  toggleEnabled() {
    this._isEnabled = !this._isEnabled;
  }
  toggle() {
    if (this._isEnabled) {
      if (this._activeTrigger.click = !this._activeTrigger.click, this._isShown()) {
        this._leave();
        return;
      }
      this._enter();
    }
  }
  dispose() {
    clearTimeout(this._timeout), l.off(this._element.closest(Gn), qn, this._hideModalHandler), this._element.getAttribute("data-bs-original-title") && this._element.setAttribute("title", this._element.getAttribute("data-bs-original-title")), this._disposePopper(), super.dispose();
  }
  show() {
    if (this._element.style.display === "none")
      throw new Error("Please use show on visible elements");
    if (!(this._isWithContent() && this._isEnabled))
      return;
    const t = l.trigger(this._element, this.constructor.eventName(ic)), s = ($s(this._element) || this._element.ownerDocument.documentElement).contains(this._element);
    if (t.defaultPrevented || !s)
      return;
    this._disposePopper();
    const i = this._getTipElement();
    this._element.setAttribute("aria-describedby", i.getAttribute("id"));
    const {
      container: r
    } = this._config;
    if (this._element.ownerDocument.documentElement.contains(this.tip) || (r.append(i), l.trigger(this._element, this.constructor.eventName(oc))), this._popper = this._createPopper(i), i.classList.add(ae), "ontouchstart" in document.documentElement)
      for (const a of [].concat(...document.body.children))
        l.on(a, "mouseover", pe);
    const o = () => {
      l.trigger(this._element, this.constructor.eventName(rc)), this._isHovered === !1 && this._leave(), this._isHovered = !1;
    };
    this._queueCallback(o, this.tip, this._isAnimated());
  }
  hide() {
    if (!this._isShown() || l.trigger(this._element, this.constructor.eventName(nc)).defaultPrevented)
      return;
    if (this._getTipElement().classList.remove(ae), "ontouchstart" in document.documentElement)
      for (const i of [].concat(...document.body.children))
        l.off(i, "mouseover", pe);
    this._activeTrigger[tc] = !1, this._activeTrigger[Pe] = !1, this._activeTrigger[jt] = !1, this._isHovered = null;
    const s = () => {
      this._isWithActiveTrigger() || (this._isHovered || this._disposePopper(), this._element.removeAttribute("aria-describedby"), l.trigger(this._element, this.constructor.eventName(sc)));
    };
    this._queueCallback(s, this.tip, this._isAnimated());
  }
  update() {
    this._popper && this._popper.update();
  }
  // Protected
  _isWithContent() {
    return !!this._getTitle();
  }
  _getTipElement() {
    return this.tip || (this.tip = this._createTipElement(this._newContent || this._getContentForTemplate())), this.tip;
  }
  _createTipElement(t) {
    const e = this._getTemplateFactory(t).toHtml();
    if (!e)
      return null;
    e.classList.remove(Re, ae), e.classList.add(`bs-${this.constructor.NAME}-auto`);
    const s = Zi(this.constructor.NAME).toString();
    return e.setAttribute("id", s), this._isAnimated() && e.classList.add(Re), e;
  }
  setContent(t) {
    this._newContent = t, this._isShown() && (this._disposePopper(), this.show());
  }
  _getTemplateFactory(t) {
    return this._templateFactory ? this._templateFactory.changeContent(t) : this._templateFactory = new qa({
      ...this._config,
      // the `content` var has to be after `this._config`
      // to override config.content in case of popover
      content: t,
      extraClass: this._resolvePossibleFunction(this._config.customClass)
    }), this._templateFactory;
  }
  _getContentForTemplate() {
    return {
      [Za]: this._getTitle()
    };
  }
  _getTitle() {
    return this._resolvePossibleFunction(this._config.title) || this._element.getAttribute("data-bs-original-title");
  }
  // Private
  _initializeOnDelegatedTarget(t) {
    return this.constructor.getOrCreateInstance(t.delegateTarget, this._getDelegateConfig());
  }
  _isAnimated() {
    return this._config.animation || this.tip && this.tip.classList.contains(Re);
  }
  _isShown() {
    return this.tip && this.tip.classList.contains(ae);
  }
  _createPopper(t) {
    const e = x(this._config.placement, [this, t, this._element]), s = hc[e.toUpperCase()];
    return an(this._element, t, this._getPopperConfig(s));
  }
  _getOffset() {
    const {
      offset: t
    } = this._config;
    return typeof t == "string" ? t.split(",").map((e) => Number.parseInt(e, 10)) : typeof t == "function" ? (e) => t(e, this._element) : t;
  }
  _resolvePossibleFunction(t) {
    return x(t, [this._element]);
  }
  _getPopperConfig(t) {
    const e = {
      placement: t,
      modifiers: [{
        name: "flip",
        options: {
          fallbackPlacements: this._config.fallbackPlacements
        }
      }, {
        name: "offset",
        options: {
          offset: this._getOffset()
        }
      }, {
        name: "preventOverflow",
        options: {
          boundary: this._config.boundary
        }
      }, {
        name: "arrow",
        options: {
          element: `.${this.constructor.NAME}-arrow`
        }
      }, {
        name: "preSetPlacement",
        enabled: !0,
        phase: "beforeMain",
        fn: (s) => {
          this._getTipElement().setAttribute("data-popper-placement", s.state.placement);
        }
      }]
    };
    return {
      ...e,
      ...x(this._config.popperConfig, [e])
    };
  }
  _setListeners() {
    const t = this._config.trigger.split(" ");
    for (const e of t)
      if (e === "click")
        l.on(this._element, this.constructor.eventName(ac), this._config.selector, (s) => {
          this._initializeOnDelegatedTarget(s).toggle();
        });
      else if (e !== ec) {
        const s = e === jt ? this.constructor.eventName(uc) : this.constructor.eventName(cc), i = e === jt ? this.constructor.eventName(dc) : this.constructor.eventName(lc);
        l.on(this._element, s, this._config.selector, (r) => {
          const o = this._initializeOnDelegatedTarget(r);
          o._activeTrigger[r.type === "focusin" ? Pe : jt] = !0, o._enter();
        }), l.on(this._element, i, this._config.selector, (r) => {
          const o = this._initializeOnDelegatedTarget(r);
          o._activeTrigger[r.type === "focusout" ? Pe : jt] = o._element.contains(r.relatedTarget), o._leave();
        });
      }
    this._hideModalHandler = () => {
      this._element && this.hide();
    }, l.on(this._element.closest(Gn), qn, this._hideModalHandler);
  }
  _fixTitle() {
    const t = this._element.getAttribute("title");
    t && (!this._element.getAttribute("aria-label") && !this._element.textContent.trim() && this._element.setAttribute("aria-label", t), this._element.setAttribute("data-bs-original-title", t), this._element.removeAttribute("title"));
  }
  _enter() {
    if (this._isShown() || this._isHovered) {
      this._isHovered = !0;
      return;
    }
    this._isHovered = !0, this._setTimeout(() => {
      this._isHovered && this.show();
    }, this._config.delay.show);
  }
  _leave() {
    this._isWithActiveTrigger() || (this._isHovered = !1, this._setTimeout(() => {
      this._isHovered || this.hide();
    }, this._config.delay.hide));
  }
  _setTimeout(t, e) {
    clearTimeout(this._timeout), this._timeout = setTimeout(t, e);
  }
  _isWithActiveTrigger() {
    return Object.values(this._activeTrigger).includes(!0);
  }
  _getConfig(t) {
    const e = q.getDataAttributes(this._element);
    for (const s of Object.keys(e))
      Qa.has(s) && delete e[s];
    return t = {
      ...e,
      ...typeof t == "object" && t ? t : {}
    }, t = this._mergeConfigObj(t), t = this._configAfterMerge(t), this._typeCheckConfig(t), t;
  }
  _configAfterMerge(t) {
    return t.container = t.container === !1 ? document.body : et(t.container), typeof t.delay == "number" && (t.delay = {
      show: t.delay,
      hide: t.delay
    }), typeof t.title == "number" && (t.title = t.title.toString()), typeof t.content == "number" && (t.content = t.content.toString()), t;
  }
  _getDelegateConfig() {
    const t = {};
    for (const [e, s] of Object.entries(this._config))
      this.constructor.Default[e] !== s && (t[e] = s);
    return t.selector = !1, t.trigger = "manual", t;
  }
  _disposePopper() {
    this._popper && (this._popper.destroy(), this._popper = null), this.tip && (this.tip.remove(), this.tip = null);
  }
  // Static
  static jQueryInterface(t) {
    return this.each(function() {
      const e = vt.getOrCreateInstance(this, t);
      if (typeof t == "string") {
        if (typeof e[t] > "u")
          throw new TypeError(`No method named "${t}"`);
        e[t]();
      }
    });
  }
}
j(vt);
const _c = "popover", mc = ".popover-header", gc = ".popover-body", Ec = {
  ...vt.Default,
  content: "",
  offset: [0, 8],
  placement: "right",
  template: '<div class="popover" role="tooltip"><div class="popover-arrow"></div><h3 class="popover-header"></h3><div class="popover-body"></div></div>',
  trigger: "click"
}, vc = {
  ...vt.DefaultType,
  content: "(null|string|element|function)"
};
class Ae extends vt {
  // Getters
  static get Default() {
    return Ec;
  }
  static get DefaultType() {
    return vc;
  }
  static get NAME() {
    return _c;
  }
  // Overrides
  _isWithContent() {
    return this._getTitle() || this._getContent();
  }
  // Private
  _getContentForTemplate() {
    return {
      [mc]: this._getTitle(),
      [gc]: this._getContent()
    };
  }
  _getContent() {
    return this._resolvePossibleFunction(this._config.content);
  }
  // Static
  static jQueryInterface(t) {
    return this.each(function() {
      const e = Ae.getOrCreateInstance(this, t);
      if (typeof t == "string") {
        if (typeof e[t] > "u")
          throw new TypeError(`No method named "${t}"`);
        e[t]();
      }
    });
  }
}
j(Ae);
const bc = "scrollspy", Ac = "bs.scrollspy", dn = `.${Ac}`, Tc = ".data-api", yc = `activate${dn}`, Xn = `click${dn}`, wc = `load${dn}${Tc}`, Oc = "dropdown-item", yt = "active", Cc = '[data-bs-spy="scroll"]', Me = "[href]", Nc = ".nav, .list-group", Qn = ".nav-link", Sc = ".nav-item", Dc = ".list-group-item", $c = `${Qn}, ${Sc} > ${Qn}, ${Dc}`, Lc = ".dropdown", Ic = ".dropdown-toggle", xc = {
  offset: null,
  // TODO: v6 @deprecated, keep it for backwards compatibility reasons
  rootMargin: "0px 0px -25%",
  smoothScroll: !1,
  target: null,
  threshold: [0.1, 0.5, 1]
}, Rc = {
  offset: "(number|null)",
  // TODO v6 @deprecated, keep it for backwards compatibility reasons
  rootMargin: "string",
  smoothScroll: "boolean",
  target: "element",
  threshold: "array"
};
class Xt extends Y {
  constructor(t, e) {
    super(t, e), this._targetLinks = /* @__PURE__ */ new Map(), this._observableSections = /* @__PURE__ */ new Map(), this._rootElement = getComputedStyle(this._element).overflowY === "visible" ? null : this._element, this._activeTarget = null, this._observer = null, this._previousScrollData = {
      visibleEntryTop: 0,
      parentScrollTop: 0
    }, this.refresh();
  }
  // Getters
  static get Default() {
    return xc;
  }
  static get DefaultType() {
    return Rc;
  }
  static get NAME() {
    return bc;
  }
  // Public
  refresh() {
    this._initializeTargetsAndObservables(), this._maybeEnableSmoothScroll(), this._observer ? this._observer.disconnect() : this._observer = this._getNewObserver();
    for (const t of this._observableSections.values())
      this._observer.observe(t);
  }
  dispose() {
    this._observer.disconnect(), super.dispose();
  }
  // Private
  _configAfterMerge(t) {
    return t.target = et(t.target) || document.body, t.rootMargin = t.offset ? `${t.offset}px 0px -30%` : t.rootMargin, typeof t.threshold == "string" && (t.threshold = t.threshold.split(",").map((e) => Number.parseFloat(e))), t;
  }
  _maybeEnableSmoothScroll() {
    this._config.smoothScroll && (l.off(this._config.target, Xn), l.on(this._config.target, Xn, Me, (t) => {
      const e = this._observableSections.get(t.target.hash);
      if (e) {
        t.preventDefault();
        const s = this._rootElement || window, i = e.offsetTop - this._element.offsetTop;
        if (s.scrollTo) {
          s.scrollTo({
            top: i,
            behavior: "smooth"
          });
          return;
        }
        s.scrollTop = i;
      }
    }));
  }
  _getNewObserver() {
    const t = {
      root: this._rootElement,
      threshold: this._config.threshold,
      rootMargin: this._config.rootMargin
    };
    return new IntersectionObserver((e) => this._observerCallback(e), t);
  }
  // The logic of selection
  _observerCallback(t) {
    const e = (o) => this._targetLinks.get(`#${o.target.id}`), s = (o) => {
      this._previousScrollData.visibleEntryTop = o.target.offsetTop, this._process(e(o));
    }, i = (this._rootElement || document.documentElement).scrollTop, r = i >= this._previousScrollData.parentScrollTop;
    this._previousScrollData.parentScrollTop = i;
    for (const o of t) {
      if (!o.isIntersecting) {
        this._activeTarget = null, this._clearActiveClass(e(o));
        continue;
      }
      const a = o.target.offsetTop >= this._previousScrollData.visibleEntryTop;
      if (r && a) {
        if (s(o), !i)
          return;
        continue;
      }
      !r && !a && s(o);
    }
  }
  _initializeTargetsAndObservables() {
    this._targetLinks = /* @__PURE__ */ new Map(), this._observableSections = /* @__PURE__ */ new Map();
    const t = h.find(Me, this._config.target);
    for (const e of t) {
      if (!e.hash || nt(e))
        continue;
      const s = h.findOne(decodeURI(e.hash), this._element);
      Pt(s) && (this._targetLinks.set(decodeURI(e.hash), e), this._observableSections.set(e.hash, s));
    }
  }
  _process(t) {
    this._activeTarget !== t && (this._clearActiveClass(this._config.target), this._activeTarget = t, t.classList.add(yt), this._activateParents(t), l.trigger(this._element, yc, {
      relatedTarget: t
    }));
  }
  _activateParents(t) {
    if (t.classList.contains(Oc)) {
      h.findOne(Ic, t.closest(Lc)).classList.add(yt);
      return;
    }
    for (const e of h.parents(t, Nc))
      for (const s of h.prev(e, $c))
        s.classList.add(yt);
  }
  _clearActiveClass(t) {
    t.classList.remove(yt);
    const e = h.find(`${Me}.${yt}`, t);
    for (const s of e)
      s.classList.remove(yt);
  }
  // Static
  static jQueryInterface(t) {
    return this.each(function() {
      const e = Xt.getOrCreateInstance(this, t);
      if (typeof t == "string") {
        if (e[t] === void 0 || t.startsWith("_") || t === "constructor")
          throw new TypeError(`No method named "${t}"`);
        e[t]();
      }
    });
  }
}
l.on(window, wc, () => {
  for (const n of h.find(Cc))
    Xt.getOrCreateInstance(n);
});
j(Xt);
const Pc = "tab", Mc = "bs.tab", bt = `.${Mc}`, kc = `hide${bt}`, Vc = `hidden${bt}`, Hc = `show${bt}`, Wc = `shown${bt}`, jc = `click${bt}`, Bc = `keydown${bt}`, Fc = `load${bt}`, Kc = "ArrowLeft", Jn = "ArrowRight", Yc = "ArrowUp", Zn = "ArrowDown", ke = "Home", ts = "End", ft = "active", es = "fade", Ve = "show", Uc = "dropdown", ei = ".dropdown-toggle", zc = ".dropdown-menu", He = `:not(${ei})`, Gc = '.list-group, .nav, [role="tablist"]', qc = ".nav-item, .list-group-item", Xc = `.nav-link${He}, .list-group-item${He}, [role="tab"]${He}`, ni = '[data-bs-toggle="tab"], [data-bs-toggle="pill"], [data-bs-toggle="list"]', We = `${Xc}, ${ni}`, Qc = `.${ft}[data-bs-toggle="tab"], .${ft}[data-bs-toggle="pill"], .${ft}[data-bs-toggle="list"]`;
class st extends Y {
  constructor(t) {
    super(t), this._parent = this._element.closest(Gc), this._parent && (this._setInitialAttributes(this._parent, this._getChildren()), l.on(this._element, Bc, (e) => this._keydown(e)));
  }
  // Getters
  static get NAME() {
    return Pc;
  }
  // Public
  show() {
    const t = this._element;
    if (this._elemIsActive(t))
      return;
    const e = this._getActiveElem(), s = e ? l.trigger(e, kc, {
      relatedTarget: t
    }) : null;
    l.trigger(t, Hc, {
      relatedTarget: e
    }).defaultPrevented || s && s.defaultPrevented || (this._deactivate(e, t), this._activate(t, e));
  }
  // Private
  _activate(t, e) {
    if (!t)
      return;
    t.classList.add(ft), this._activate(h.getElementFromSelector(t));
    const s = () => {
      if (t.getAttribute("role") !== "tab") {
        t.classList.add(Ve);
        return;
      }
      t.removeAttribute("tabindex"), t.setAttribute("aria-selected", !0), this._toggleDropDown(t, !0), l.trigger(t, Wc, {
        relatedTarget: e
      });
    };
    this._queueCallback(s, t, t.classList.contains(es));
  }
  _deactivate(t, e) {
    if (!t)
      return;
    t.classList.remove(ft), t.blur(), this._deactivate(h.getElementFromSelector(t));
    const s = () => {
      if (t.getAttribute("role") !== "tab") {
        t.classList.remove(Ve);
        return;
      }
      t.setAttribute("aria-selected", !1), t.setAttribute("tabindex", "-1"), this._toggleDropDown(t, !1), l.trigger(t, Vc, {
        relatedTarget: e
      });
    };
    this._queueCallback(s, t, t.classList.contains(es));
  }
  _keydown(t) {
    if (![Kc, Jn, Yc, Zn, ke, ts].includes(t.key))
      return;
    t.stopPropagation(), t.preventDefault();
    const e = this._getChildren().filter((i) => !nt(i));
    let s;
    if ([ke, ts].includes(t.key))
      s = e[t.key === ke ? 0 : e.length - 1];
    else {
      const i = [Jn, Zn].includes(t.key);
      s = cn(e, t.target, i, !0);
    }
    s && (s.focus({
      preventScroll: !0
    }), st.getOrCreateInstance(s).show());
  }
  _getChildren() {
    return h.find(We, this._parent);
  }
  _getActiveElem() {
    return this._getChildren().find((t) => this._elemIsActive(t)) || null;
  }
  _setInitialAttributes(t, e) {
    this._setAttributeIfNotExists(t, "role", "tablist");
    for (const s of e)
      this._setInitialAttributesOnChild(s);
  }
  _setInitialAttributesOnChild(t) {
    t = this._getInnerElement(t);
    const e = this._elemIsActive(t), s = this._getOuterElement(t);
    t.setAttribute("aria-selected", e), s !== t && this._setAttributeIfNotExists(s, "role", "presentation"), e || t.setAttribute("tabindex", "-1"), this._setAttributeIfNotExists(t, "role", "tab"), this._setInitialAttributesOnTargetPanel(t);
  }
  _setInitialAttributesOnTargetPanel(t) {
    const e = h.getElementFromSelector(t);
    e && (this._setAttributeIfNotExists(e, "role", "tabpanel"), t.id && this._setAttributeIfNotExists(e, "aria-labelledby", `${t.id}`));
  }
  _toggleDropDown(t, e) {
    const s = this._getOuterElement(t);
    if (!s.classList.contains(Uc))
      return;
    const i = (r, o) => {
      const a = h.findOne(r, s);
      a && a.classList.toggle(o, e);
    };
    i(ei, ft), i(zc, Ve), s.setAttribute("aria-expanded", e);
  }
  _setAttributeIfNotExists(t, e, s) {
    t.hasAttribute(e) || t.setAttribute(e, s);
  }
  _elemIsActive(t) {
    return t.classList.contains(ft);
  }
  // Try to get the inner element (usually the .nav-link)
  _getInnerElement(t) {
    return t.matches(We) ? t : h.findOne(We, t);
  }
  // Try to get the outer element (usually the .nav-item)
  _getOuterElement(t) {
    return t.closest(qc) || t;
  }
  // Static
  static jQueryInterface(t) {
    return this.each(function() {
      const e = st.getOrCreateInstance(this);
      if (typeof t == "string") {
        if (e[t] === void 0 || t.startsWith("_") || t === "constructor")
          throw new TypeError(`No method named "${t}"`);
        e[t]();
      }
    });
  }
}
l.on(document, jc, ni, function(n) {
  ["A", "AREA"].includes(this.tagName) && n.preventDefault(), !nt(this) && st.getOrCreateInstance(this).show();
});
l.on(window, Fc, () => {
  for (const n of h.find(Qc))
    st.getOrCreateInstance(n);
});
j(st);
const Jc = "toast", Zc = "bs.toast", ot = `.${Zc}`, tl = `mouseover${ot}`, el = `mouseout${ot}`, nl = `focusin${ot}`, sl = `focusout${ot}`, il = `hide${ot}`, rl = `hidden${ot}`, ol = `show${ot}`, al = `shown${ot}`, cl = "fade", ns = "hide", ce = "show", le = "showing", ll = {
  animation: "boolean",
  autohide: "boolean",
  delay: "number"
}, ul = {
  animation: !0,
  autohide: !0,
  delay: 5e3
};
class Qt extends Y {
  constructor(t, e) {
    super(t, e), this._timeout = null, this._hasMouseInteraction = !1, this._hasKeyboardInteraction = !1, this._setListeners();
  }
  // Getters
  static get Default() {
    return ul;
  }
  static get DefaultType() {
    return ll;
  }
  static get NAME() {
    return Jc;
  }
  // Public
  show() {
    if (l.trigger(this._element, ol).defaultPrevented)
      return;
    this._clearTimeout(), this._config.animation && this._element.classList.add(cl);
    const e = () => {
      this._element.classList.remove(le), l.trigger(this._element, al), this._maybeScheduleHide();
    };
    this._element.classList.remove(ns), Yt(this._element), this._element.classList.add(ce, le), this._queueCallback(e, this._element, this._config.animation);
  }
  hide() {
    if (!this.isShown() || l.trigger(this._element, il).defaultPrevented)
      return;
    const e = () => {
      this._element.classList.add(ns), this._element.classList.remove(le, ce), l.trigger(this._element, rl);
    };
    this._element.classList.add(le), this._queueCallback(e, this._element, this._config.animation);
  }
  dispose() {
    this._clearTimeout(), this.isShown() && this._element.classList.remove(ce), super.dispose();
  }
  isShown() {
    return this._element.classList.contains(ce);
  }
  // Private
  _maybeScheduleHide() {
    this._config.autohide && (this._hasMouseInteraction || this._hasKeyboardInteraction || (this._timeout = setTimeout(() => {
      this.hide();
    }, this._config.delay)));
  }
  _onInteraction(t, e) {
    switch (t.type) {
      case "mouseover":
      case "mouseout": {
        this._hasMouseInteraction = e;
        break;
      }
      case "focusin":
      case "focusout": {
        this._hasKeyboardInteraction = e;
        break;
      }
    }
    if (e) {
      this._clearTimeout();
      return;
    }
    const s = t.relatedTarget;
    this._element === s || this._element.contains(s) || this._maybeScheduleHide();
  }
  _setListeners() {
    l.on(this._element, tl, (t) => this._onInteraction(t, !0)), l.on(this._element, el, (t) => this._onInteraction(t, !1)), l.on(this._element, nl, (t) => this._onInteraction(t, !0)), l.on(this._element, sl, (t) => this._onInteraction(t, !1));
  }
  _clearTimeout() {
    clearTimeout(this._timeout), this._timeout = null;
  }
  // Static
  static jQueryInterface(t) {
    return this.each(function() {
      const e = Qt.getOrCreateInstance(this, t);
      if (typeof t == "string") {
        if (typeof e[t] > "u")
          throw new TypeError(`No method named "${t}"`);
        e[t](this);
      }
    });
  }
}
be(Qt);
j(Qt);
const El = /* @__PURE__ */ Object.freeze(/* @__PURE__ */ Object.defineProperty({
  __proto__: null,
  Alert: zt,
  Button: Gt,
  Carousel: kt,
  Collapse: xt,
  Dropdown: K,
  Modal: gt,
  Offcanvas: Q,
  Popover: Ae,
  ScrollSpy: Xt,
  Tab: st,
  Toast: Qt,
  Tooltip: vt
}, Symbol.toStringTag, { value: "Module" }));
function dl(n, t) {
  const e = new URL(document.URL);
  for (let s of document.querySelectorAll(n)) {
    const i = new URL(s.href);
    e.pathname.startsWith(i.pathname) ? (s.classList.add("active"), s.setAttribute("hx-get", s.dataset.url)) : (s.classList.remove("active"), s.setAttribute("hx-get", s.dataset.url + s.dataset.path)), htmx.process(s);
  }
}
function hl(n) {
  document.querySelector(n).innerHTML = "";
}
function fl(n) {
  const t = document.getElementById(n);
  return t && (t.style.display = "none", t.replaceChildren()), t;
}
async function si(n, t, e, s) {
  const i = {
    LOGIN_BAD_CREDENTIALS: "Incorrect credentials, please verify the email address and password.",
    REGISTER_USER_ALREADY_EXISTS: "Email address already registered, did you forget your password?",
    RESET_PASSWORD_BAD_TOKEN: "Invalid or expired link, did you click an old or already used link?"
  }, r = fl(e), o = {};
  for (const c of n.elements)
    c.name != "" && (o[c.name] = c.value);
  const a = await fetch(
    n.action,
    {
      method: n.method,
      headers: {
        "Content-Type": s ? "application/json" : "application/x-www-form-urlencoded"
      },
      body: s ? JSON.stringify(o) : new URLSearchParams(o)
    }
  );
  if (a.ok)
    window.location.href = t;
  else {
    const c = a.headers.get("content-type");
    if (c && c.indexOf("application/json") != -1) {
      const d = await a.json(), u = d.detail;
      if (Array.isArray(u))
        for (let f of u)
          r.appendChild(document.createTextNode(
            `${f.msg}, `
          ));
      else if (typeof u == "object") {
        const f = u.reason;
        r.appendChild(document.createTextNode(f));
      } else if (u) {
        const f = i[u] || u;
        r.appendChild(document.createTextNode(f));
      } else
        r.appendChild(document.createTextNode(
          `Unexpected error: ${a.status} ${a.statusText}`
        )), r.appendChild(document.createElement("pre")).textContent = JSON.stringify(d);
    } else {
      const d = await a.text();
      r.appendChild(document.createTextNode(d));
    }
    r.style.display = "block";
  }
}
async function pl(n, t, e = "result") {
  return await si(n, t, e, !1);
}
async function _l(n, t, e = "result") {
  return await si(n, t, e, !0);
}
function ml(n) {
  const t = document.querySelector("#alert-error"), s = document.querySelector("#alert-error-template").content.cloneNode(!0);
  s.querySelector("#alert-error-text").textContent = n, t.replaceChildren(s);
}
function gl(n) {
  if (n) {
    let t = document.querySelector(`${n}-tab`);
    new st(t).show();
  }
}
window.activate = dl;
window.clearContent = hl;
window.openTab = gl;
window.showAlert = ml;
window.submitForm = pl;
window.submitFormAsJSON = _l;
export {
  El as bootstrap
};
