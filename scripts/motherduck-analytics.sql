-- MotherDuck analytics queries for legal brief pipeline
-- Database: legal_briefs
-- Table: brief_builds

-- All builds in last 30 days
SELECT 
    pdf_name,
    branch,
    word_count,
    built_at,
    pdf_url
FROM legal_briefs.brief_builds
WHERE built_at >= NOW() - INTERVAL '30 days'
ORDER BY built_at DESC;

-- Word count trend by brief
SELECT 
    pdf_name,
    MIN(word_count) as min_words,
    MAX(word_count) as max_words,
    AVG(word_count)::INTEGER as avg_words,
    COUNT(*) as build_count,
    MAX(built_at) as last_built
FROM legal_briefs.brief_builds
GROUP BY pdf_name
ORDER BY last_built DESC;

-- Build frequency by repo
SELECT 
    repo,
    COUNT(*) as total_builds,
    MAX(built_at) as last_build
FROM legal_briefs.brief_builds
GROUP BY repo
ORDER BY total_builds DESC;

-- Latest build per brief (current state)
SELECT DISTINCT ON (pdf_name)
    pdf_name,
    branch,
    word_count,
    built_at,
    pdf_url,
    commit_sha
FROM legal_briefs.brief_builds
ORDER BY pdf_name, built_at DESC;
