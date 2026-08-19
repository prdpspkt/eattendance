/*
 * Bikram Sambat <-> Gregorian for date filters.
 *
 * The office reads dates in BS; the database stores AD. Every date filter
 * therefore carries two boxes - a native date picker and a BS text box - and
 * this keeps them in step as you type, so neither has to be worked out on
 * paper.
 *
 * The calendar table is not computed here. BS month lengths are published data
 * that vary year to year, so the server hands the same table it uses itself
 * down to the page (see core/nepali_date.py). A second table maintained here
 * would eventually disagree with the server's for some year nobody tested, and
 * a filter that disagrees with the report it filters is worse than no filter.
 *
 * Everything below degrades safely: with JavaScript off, the BS box is still
 * submitted and the server converts it.
 */
(function () {
    'use strict';

    const dataElement = document.getElementById('bs-calendar-data');
    if (!dataElement) { return; }

    let CALENDAR;
    try {
        CALENDAR = JSON.parse(dataElement.textContent);
    } catch (error) {
        return;
    }

    const MS_PER_DAY = 86400000;
    const anchor = Date.UTC(CALENDAR.anchorAd[0], CALENDAR.anchorAd[1] - 1, CALENDAR.anchorAd[2]);

    function pad(value) {
        return String(value).padStart(2, '0');
    }

    /* Days from BS minYear-01-01 to the given BS date, or null if off-table. */
    function bsToOffset(year, month, day) {
        const index = year - CALENDAR.minYear;
        const months = CALENDAR.monthDays[index];
        if (!months || month < 1 || month > 12) { return null; }
        if (day < 1 || day > months[month - 1]) { return null; }

        let offset = 0;
        for (let y = 0; y < index; y += 1) {
            offset += CALENDAR.monthDays[y].reduce((sum, days) => sum + days, 0);
        }
        for (let m = 0; m < month - 1; m += 1) {
            offset += months[m];
        }
        return offset + day - 1;
    }

    function bsToAd(year, month, day) {
        const offset = bsToOffset(year, month, day);
        if (offset === null) { return null; }
        const stamp = new Date(anchor + offset * MS_PER_DAY);
        return [stamp.getUTCFullYear(), stamp.getUTCMonth() + 1, stamp.getUTCDate()];
    }

    function adToBs(year, month, day) {
        const stamp = Date.UTC(year, month - 1, day);
        let remaining = Math.round((stamp - anchor) / MS_PER_DAY);
        if (remaining < 0) { return null; }

        for (let index = 0; index < CALENDAR.monthDays.length; index += 1) {
            const months = CALENDAR.monthDays[index];
            const yearLength = months.reduce((sum, days) => sum + days, 0);
            if (remaining >= yearLength) {
                remaining -= yearLength;
                continue;
            }
            for (let m = 0; m < 12; m += 1) {
                if (remaining < months[m]) {
                    return [CALENDAR.minYear + index, m + 1, remaining + 1];
                }
                remaining -= months[m];
            }
        }
        return null;
    }

    function parseBsText(text) {
        const parts = String(text || '').trim().replace(/[/.]/g, '-').split('-');
        if (parts.length !== 3) { return null; }
        const numbers = parts.map(Number);
        if (numbers.some((value) => !Number.isInteger(value))) { return null; }
        return numbers;
    }

    function describe(bs) {
        if (!bs) { return ''; }
        return bs[2] + ' ' + CALENDAR.monthNames[bs[1] - 1] + ' ' + bs[0];
    }

    /* Pair one AD input with its BS twin. */
    function link(adInput) {
        const bsInput = document.querySelector('[data-bs-for="' + adInput.id + '"]');
        if (!bsInput) { return; }

        const hint = document.querySelector('[data-bs-hint-for="' + adInput.id + '"]');
        let syncing = false;

        function showHint(bs, valid) {
            if (!hint) { return; }
            if (!bs) {
                hint.textContent = valid === false ? 'Not a date on the Nepali calendar' : '';
                hint.classList.toggle('text-danger', valid === false);
                return;
            }
            hint.textContent = describe(bs);
            hint.classList.remove('text-danger');
        }

        function fromAd() {
            if (syncing) { return; }
            syncing = true;
            const value = adInput.value;
            if (!value) {
                bsInput.value = '';
                showHint(null);
            } else {
                const parts = value.split('-').map(Number);
                const bs = adToBs(parts[0], parts[1], parts[2]);
                bsInput.value = bs ? bs[0] + '-' + pad(bs[1]) + '-' + pad(bs[2]) : '';
                showHint(bs, bs !== null);
            }
            syncing = false;
        }

        function fromBs() {
            if (syncing) { return; }
            syncing = true;
            const parts = parseBsText(bsInput.value);
            if (!parts) {
                showHint(null, bsInput.value ? false : undefined);
            } else {
                const ad = bsToAd(parts[0], parts[1], parts[2]);
                if (ad) {
                    adInput.value = ad[0] + '-' + pad(ad[1]) + '-' + pad(ad[2]);
                    showHint(parts, true);
                } else {
                    showHint(null, false);
                }
            }
            syncing = false;
        }

        adInput.addEventListener('change', fromAd);
        adInput.addEventListener('input', fromAd);
        bsInput.addEventListener('input', fromBs);
        bsInput.addEventListener('change', fromBs);
        fromAd();
    }

    document.querySelectorAll('input[type="date"][data-bs-paired]').forEach(link);
}());
