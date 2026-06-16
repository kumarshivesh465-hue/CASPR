/**
 * FIREBASE CLOUD FUNCTION — DATA FUSION ENGINE
 * =============================================
 * Triggers whenever new rover data is written to Firebase.
 * Fetches the latest satellite indices for the same field,
 * fuses both datasets, and writes recommendations.
 *
 * DEPLOY:
 *   npm install -g firebase-tools
 *   firebase login
 *   firebase init functions   (choose JavaScript, this project)
 *   # paste this file into functions/index.js
 *   firebase deploy --only functions
 */

const functions  = require("firebase-functions");
const admin      = require("firebase-admin");
admin.initializeApp();

const db = admin.database();

// ── TRIGGER: fires on every new rover reading ─────────────────────────────────
exports.fuseAndRecommend = functions.database
    .ref("/rover/{fieldId}/latest")
    .onWrite(async (change, context) => {
        const fieldId = context.params.fieldId;
        const rover   = change.after.val();
        if (!rover) return null;

        console.log(`[Fusion] New rover data for field: ${fieldId}`);

        // Fetch latest satellite indices
        const satSnap = await db.ref(`/satellite/${fieldId}/latest`).once("value");
        const sat     = satSnap.val();

        // Build fused record
        const fused = {
            timestamp: new Date().toISOString(),
            field_id:  fieldId,
            rover:     rover,
            satellite: sat || null,
        };

        // Generate recommendations
        const recs = generateRecommendations(rover, sat);
        fused.recommendations = recs;

        // Write fused record
        await db.ref(`/fused/${fieldId}/latest`).set(fused);
        await db.ref(`/fused/${fieldId}/history/${Date.now()}`).set(fused);

        // Write recommendations separately for easy dashboard reads
        await db.ref(`/recommendations/${fieldId}/latest`).set({
            timestamp: fused.timestamp,
            items:     recs,
        });

        console.log(`[Fusion] ✅ ${recs.length} recommendations written for ${fieldId}`);
        return null;
    });


// ── RECOMMENDATION ENGINE ─────────────────────────────────────────────────────
function generateRecommendations(rover, sat) {
    const recs = [];

    // ── NITROGEN ──────────────────────────────────────────────────────────────
    const n = parseFloat(rover.npk_n);
    if (!isNaN(n)) {
        if (n < 50) {
            recs.push({
                category: "Nutrition",
                severity: "critical",
                message:  `Very low Nitrogen (${n} mg/kg). Apply urea at 40–60 kg/acre urgently.`,
                action:   "apply_urea",
                value:    n,
                unit:     "mg/kg",
            });
        } else if (n < 100) {
            recs.push({
                category: "Nutrition",
                severity: "warning",
                message:  `Low Nitrogen (${n} mg/kg). Consider top-dressing with 20–30 kg urea/acre.`,
                action:   "monitor_nitrogen",
                value:    n,
                unit:     "mg/kg",
            });
        }
    }

    // ── PHOSPHORUS ────────────────────────────────────────────────────────────
    const p = parseFloat(rover.npk_p);
    if (!isNaN(p) && p < 25) {
        recs.push({
            category: "Nutrition",
            severity: p < 10 ? "critical" : "warning",
            message:  `Low Phosphorus (${p} mg/kg). Apply DAP or SSP at 25–35 kg/acre.`,
            action:   "apply_phosphorus",
            value:    p,
            unit:     "mg/kg",
        });
    }

    // ── POTASSIUM ─────────────────────────────────────────────────────────────
    const k = parseFloat(rover.npk_k);
    if (!isNaN(k) && k < 110) {
        recs.push({
            category: "Nutrition",
            severity: k < 60 ? "critical" : "warning",
            message:  `Low Potassium (${k} mg/kg). Apply MOP at 20–25 kg/acre.`,
            action:   "apply_potassium",
            value:    k,
            unit:     "mg/kg",
        });
    }

    // ── pH ────────────────────────────────────────────────────────────────────
    const ph = parseFloat(rover.ph);
    if (!isNaN(ph)) {
        if (ph < 5.5) {
            recs.push({
                category: "Soil pH",
                severity: "critical",
                message:  `Soil too acidic (pH ${ph.toFixed(1)}). Apply lime at 200–400 kg/acre to raise pH.`,
                action:   "apply_lime",
                value:    ph,
            });
        } else if (ph > 8.0) {
            recs.push({
                category: "Soil pH",
                severity: "warning",
                message:  `Soil alkaline (pH ${ph.toFixed(1)}). Apply gypsum or sulphur to lower pH.`,
                action:   "apply_sulphur",
                value:    ph,
            });
        }
    }

    // ── SOIL MOISTURE ─────────────────────────────────────────────────────────
    const moist = parseFloat(rover.soil_moisture);
    const ndwi  = sat ? parseFloat(sat.indices?.NDWI?.mean) : NaN;

    if (!isNaN(moist)) {
        if (moist < 25) {
            const satelliteNote = (!isNaN(ndwi) && ndwi < -0.1)
                ? ` Satellite NDWI (${ndwi.toFixed(2)}) also confirms water stress.`
                : "";
            recs.push({
                category: "Irrigation",
                severity: moist < 15 ? "critical" : "warning",
                message:  `Low soil moisture (${moist.toFixed(0)}%).${satelliteNote} Irrigate immediately.`,
                action:   "irrigate",
                value:    moist,
                unit:     "%",
            });
        } else if (moist > 80) {
            recs.push({
                category: "Irrigation",
                severity: "warning",
                message:  `Excess soil moisture (${moist.toFixed(0)}%). Risk of waterlogging and root rot. Check drainage.`,
                action:   "check_drainage",
                value:    moist,
                unit:     "%",
            });
        }
    }

    // ── NDVI (satellite) ──────────────────────────────────────────────────────
    if (sat && sat.indices) {
        const ndvi = parseFloat(sat.indices.NDVI?.mean);
        if (!isNaN(ndvi)) {
            if (ndvi < 0.2) {
                recs.push({
                    category: "Crop Health",
                    severity: "critical",
                    message:  `Very low NDVI (${ndvi.toFixed(2)}) — crop stress or bare soil detected. Check field immediately.`,
                    action:   "field_inspection",
                    value:    ndvi,
                });
            } else if (ndvi < 0.35) {
                recs.push({
                    category: "Crop Health",
                    severity: "warning",
                    message:  `Below-average NDVI (${ndvi.toFixed(2)}). Canopy density is low — consider fertilising.`,
                    action:   "fertilise",
                    value:    ndvi,
                });
            }
        }

        // ── EVI cross-check ───────────────────────────────────────────────────
        const evi = parseFloat(sat.indices.EVI?.mean);
        if (!isNaN(evi) && !isNaN(ndvi) && evi < ndvi * 0.7) {
            recs.push({
                category: "Crop Health",
                severity: "info",
                message:  `EVI (${evi.toFixed(2)}) is significantly lower than NDVI (${ndvi.toFixed(2)}). Possible atmospheric haze or sparse canopy.`,
                action:   "monitor",
                value:    evi,
            });
        }
    }

    // ── AIR TEMPERATURE ───────────────────────────────────────────────────────
    const temp = parseFloat(rover.air_temp);
    if (!isNaN(temp)) {
        if (temp > 42) {
            recs.push({
                category: "Weather",
                severity: "warning",
                message:  `High air temperature (${temp.toFixed(1)}°C). Risk of heat stress — irrigate in early morning.`,
                action:   "irrigate_morning",
                value:    temp,
                unit:     "°C",
            });
        }
    }

    // ── OVERALL HEALTH SUMMARY ────────────────────────────────────────────────
    const critical = recs.filter(r => r.severity === "critical").length;
    const warnings = recs.filter(r => r.severity === "warning").length;

    if (critical === 0 && warnings === 0) {
        recs.push({
            category: "Overall",
            severity: "info",
            message:  "Field conditions look healthy. Continue regular monitoring.",
            action:   "monitor",
        });
    } else {
        recs.unshift({
            category: "Summary",
            severity: critical > 0 ? "critical" : "warning",
            message:  `${critical} critical issue(s), ${warnings} warning(s) detected.`,
            action:   "review",
        });
    }

    return recs;
}
