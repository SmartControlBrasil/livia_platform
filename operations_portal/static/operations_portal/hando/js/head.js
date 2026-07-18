var savedConfig = localStorage.getItem("__CONFIG__");
var defaultConfig = {
    theme: "light",
};
const config = Object.assign(defaultConfig, JSON.parse(savedConfig));


const saveState = () => {
    localStorage.setItem("__CONFIG__", JSON.stringify(config));
}

const changeThemeMode = (theme) => {
    const html = document.getElementsByTagName('html')[0];
    html.setAttribute('data-bs-theme', theme)
    config.theme = theme;
    saveState();
}

const init = () => {
    window.addEventListener('load', initTheme)
}

const initTheme = () => {
    //TopBar Light Dark
    var themeColorToggle = document.getElementById('light-dark-mode');

    if (themeColorToggle) {
        themeColorToggle.addEventListener('click', function (e) {
            if (config.theme === 'light') {
                changeThemeMode('dark');
            } else {
                changeThemeMode('light');
            }
        });
    }

}
changeThemeMode(config.theme);

init();