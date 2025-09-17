const html = `
<div style="position: fixed; top: 10px; right: 10px; z-index: 1000;">
    <button id="menuToggle" style="background: none; color: black; border: none; font-size: 24px; cursor: pointer;">
        &#x22EE;
    </button>

    <!-- Dropdown Menu -->
    <div id="menuDropdown"
        style="display: none; position: absolute; right: 0; top: 35px; background: white; border: 1px solid #ccc; border-radius: 4px; box-shadow: 0 2px 6px rgba(0,0,0,0.2); min-width: 120px;">
        <button onclick="logout()">Logout</button>
    </div>
</div>
`;

document.body.insertAdjacentHTML("beforeend", html);



const toggle = document.getElementById("menuToggle");
const menu = document.getElementById("menuDropdown");

toggle.addEventListener("click", () => {
    menu.style.display = (menu.style.display === "none" || menu.style.display === "") ? "block" : "none";
});

// Close the menu if clicked outside
document.addEventListener("click", (e) => {
    if (!toggle.contains(e.target) && !menu.contains(e.target)) {
        menu.style.display = "none";
    }
});

logout = () => {
    location.href = 'v1/logout';
};