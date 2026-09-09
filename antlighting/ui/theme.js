.pragma library

// AntLighting palette.  Dark is the default; light is derived from the same
// accent so the two themes feel like the same product.

function theme(mode, systemDark) {
    var dark = mode === "system" ? systemDark : (mode === "dark");
    return dark ? darkPalette : lightPalette;
}

var darkPalette = {
    dark: true,
    bg: "#0B0F14",
    bgAlt: "#11171F",
    surface: "#161E28",
    surfaceHover: "#1D2733",
    border: "#243040",
    text: "#E8EEF5",
    textDim: "#8B9BAD",
    textFaint: "#5C6B7C",
    accent: "#FFC61A",
    accentSoft: "#FFE08A",
    bolt: "#FFE9A8",
    success: "#3FD07F",
    warning: "#FFB020",
    danger: "#FF5C5C",
    info: "#4AA8FF",
    antBody: "#26313F",
    antBodyLight: "#384859",
    antOutline: "#0A0E13",
};

var lightPalette = {
    dark: false,
    bg: "#F4F6F9",
    bgAlt: "#FFFFFF",
    surface: "#FFFFFF",
    surfaceHover: "#EEF2F7",
    border: "#DCE3EC",
    text: "#16202B",
    textDim: "#5A6B7C",
    textFaint: "#8C9AA8",
    accent: "#E8A400",
    accentSoft: "#FFD35E",
    bolt: "#C98A00",
    success: "#12965A",
    warning: "#C87F00",
    danger: "#D93B3B",
    info: "#1673D1",
    antBody: "#3C4B5C",
    antBodyLight: "#55677C",
    antOutline: "#202933",
};

var statusColors = {
    working: "success",
    slow: "warning",
    unknown: "textFaint",
    testing: "info",
    failed: "danger",
    timeout: "danger",
    invalid: "danger",
    unavailable: "danger",
    auto: "accent",
};

function statusColor(palette, status) {
    var key = statusColors[status] || "textDim";
    return palette[key];
}

function stateAccent(palette, state) {
    if (state === "connected") return palette.success;
    if (state === "error") return palette.danger;
    if (state === "connecting" || state === "disconnecting") return palette.accent;
    return palette.textFaint;
}

function fontSizes() {
    return {
        display: 40,
        title: 20,
        subtitle: 15,
        body: 14,
        small: 12,
        tiny: 11,
    };
}

var radius = {
    sm: 6,
    md: 12,
    lg: 18,
    pill: 999,
};
