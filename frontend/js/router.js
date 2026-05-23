async function loadSection(category, page){
    const content = document.getElementById("content");
    if(!content) return;

    let html = "";
    try {
        const res = await fetch(`pages/${page}.html`);
        if(!res.ok) throw new Error(`section_not_found:${page}`);
        html = await res.text();
        if(!html.trim()) throw new Error(`section_empty:${page}`);
    } catch {
        html = `
            <section class="enterprise-page">
                <p class="eyebrow">WorkforceOS</p>
                <h2>Operational workspace is calibrating</h2>
                <p class="section-desc">This intelligence surface is ready for enterprise content. Return to the command center or choose another module while the workspace synchronizes.</p>
                <div class="feature-grid">
                    <article class="feature-card">
                        <h3>Operational Continuity</h3>
                        <p>The platform keeps navigation stable even when a module needs a fresh sync.</p>
                    </article>
                    <article class="feature-card">
                        <h3>AI Guidance</h3>
                        <p>WorkforceOS keeps recruiter workflows connected to candidate, security, and executive intelligence.</p>
                    </article>
                </div>
            </section>
        `;
    }

    content.innerHTML = `
        <div class="layout">
            <div class="sidebar">
                ${getSidebar(category)}
            </div>
            <div class="main-content fade-in">
                ${html}
            </div>
        </div>
    `;

    // AUTO LOAD FEATURES
    if(page === "ranking") loadRanking();
    if(page === "analytics") loadAnalytics();
}

function loadPage(page){
    const products = new Set(["sourcing", "tech-skills", "soft-skills", "facecode", "hiring-challenges"]);
    const features = new Set(["analytics", "chat", "skill-based", "proctoring", "experience", "technical-screening", "browser", "screen"]);
    const normalized = page === "screen" ? "technical-screening" : page;
    if(products.has(normalized)) return loadSection("products", normalized);
    if(features.has(normalized)) return loadSection("features", normalized);
    return loadSection("products", normalized);
}
function getSidebar(category){

    const menus = {

        products: [
            ["interview", "Interview Agent"],
            ["sourcing", "Sourcing"],
            ["tech-skills", "Tech Skills"],
            ["soft-skills", "Soft Skills"],
            ["facecode", "Facecode"]
        ],

        features: [
            ["analytics", "Analytics"],
            ["chat", "Chat AI"],
            ["skill-based", "Skill Assessment"],
            ["proctoring", "Proctoring"],
            ["experience", "Experience"],
            ["technical-screening", "Screening"],
            ["browser", "Browser"]
        ],

        solutions: [
            ["recruiters", "Recruiters"],
            ["managers", "Managers"],
            ["university", "University"],
            ["remote", "Remote"],
            ["api", "API"],
            ["challenges", "Challenges"]
        ],

        resources: [
            ["customers", "Customers"],
            ["job-description", "Job Description"],
            ["tests", "Tests"],
            ["demo", "Demo"]
        ]
    };

    let html = "<h3>" + category.toUpperCase() + "</h3>";

    menus[category].forEach(item => {
        html += `
            <div onclick="loadSection('${category}','${item[0]}')">
                ${item[1]}
            </div>
        `;
    });

    return html;
}
