ALTER TABLE chains
ADD COLUMN IF NOT EXISTS active BOOLEAN DEFAULT TRUE;

INSERT INTO chains (code, active) VALUES
('boso', TRUE),
('brodokomerc', TRUE),
('dm', TRUE),
('eurospin', TRUE),
('kaufland', TRUE),
('konzum', TRUE),
('ktc', TRUE),
('lidl', TRUE),
('lorenco', TRUE),
('metro', TRUE),
('ntl', TRUE),
('plodine', TRUE),
('ribola', TRUE),
('roto', TRUE),
('spar', TRUE),
('studenac', TRUE),
('tommy', TRUE),
('trgocentar', TRUE),
('trgovina-krk', TRUE),
('vrutak', TRUE),
('zabac', TRUE)
ON CONFLICT (code) DO NOTHING;
