BEGIN;

-- Ensure a clean state for tables and columns that will be created/renamed
DROP TABLE IF EXISTS g_categories CASCADE;
DROP TABLE IF EXISTS old_categories CASCADE;
ALTER TABLE g_products DROP CONSTRAINT IF EXISTS fk_category;
ALTER TABLE g_products DROP COLUMN IF EXISTS old_category_id;
ALTER TABLE g_products DROP COLUMN IF EXISTS old_category_name; -- Ensure this is dropped if it exists from a previous failed attempt

-- Rename g_products.category_id to old_category_id
-- This step will only succeed if category_id exists. If it doesn't, it means the table was just created or already cleaned.
-- We need to handle the case where g_products might not have a category_id yet (e.g., fresh install before V016).
-- For robustness, we can add a temporary nullable column first, then rename.
ALTER TABLE g_products ADD COLUMN IF NOT EXISTS old_category_id INTEGER;
UPDATE g_products gp SET old_category_id = gp.category_id WHERE gp.category_id IS NOT NULL;
ALTER TABLE g_products DROP COLUMN IF EXISTS category_id; -- Drop the original if it exists

-- Rename categories table to old_categories
ALTER TABLE categories RENAME TO old_categories;

-- Create the g_categories table
CREATE TABLE IF NOT EXISTS g_categories (
    id INTEGER PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    array_of_old_ids INTEGER[]
);

-- Insert data into g_categories
INSERT INTO g_categories (id, name, array_of_old_ids) VALUES
(1,'Voće i povrće','{16,23,32,84,93,111,113,121,126,138,140,142,157,159,170,171,179,184,190,206,209,215,221,260,263,280,290,293,296,303,321,324,329,355,370,377,392,398,403,415,423,447,453,454,463,478,485,489,508,509,530,535,546,555,567,584,586,596,601,617,619,642,658,668,707,708,710,718,719,724,749,760,765,771,788}'),
(2,'Meso i perad','{15,37,134,139,154,220,222,223,236,273,275,284,310,339,349,365,386,391,410,424,427,455,464,506,542,545,550,563,611,615,626,644,753,754,809,818,826}'),
(3,'Riba i plodovi mora','{4,62,103,112,181,182,203,248,264,282,330,476,487,497,513,523,569,629,646,679,709,725,786,812,814}'),
(4,'Mlijeko i mliječni proizvodi','{31,78,106,147,151,187,265,268,283,291,307,333,345,368,412,420,429,430,438,462,492,500,502,521,534,548,608,680,687,693,702,703,731,747,750,755,784,825}'),
(5,'Kruh i peciva','{27,52,63,243,458,459,683,711,715,743,791,816}'),
(6,'Tjestenina, riža i žitarice','{13,42,61,73,75,81,82,100,124,127,129,148,185,193,194,207,210,225,227,240,246,251,285,298,305,311,314,315,336,353,358,359,362,389,393,395,400,405,425,434,441,446,457,474,512,518,528,531,541,554,571,578,583,593,618,620,633,653,657,670,671,673,705,706,721,722,739,741,757,776,777,795,810}'),
(7,'Slatkiši i grickalice','{1,3,7,24,41,101,102,128,153,165,166,169,176,192,211,228,233,253,266,277,300,306,319,346,348,366,373,385,404,419,460,461,496,503,504,519,525,527,538,549,553,589,600,607,609,616,627,636,651,654,661,664,732,736,746,762,764,767,769,778,793,805}'),
(8,'Kava, čaj i kakao','{14,35,54,58,68,89,152,178,196,232,238,239,242,250,252,257,270,271,274,276,294,334,337,354,363,421,449,456,473,511,533,537,559,560,574,602,613,614,638,645,649,689,720,761,797,798,804,811,819,822}'),
(9,'Bezalkoholna pića','{5,64,66,72,117,130,146,161,163,167,195,208,213,255,269,292,302,331,364,372,396,406,409,416,539,556,585,592,595,637}'),
(10,'Alkoholna pića','{22,26,47,83,96,141,201,218,230,244,287,288,301,312,313,318,328,394,522,594,605,641,647,656,659,669,775,785}'),
(11,'Ulja, ocat i namazi','{6,9,17,19,53,60,85,86,110,115,122,123,133,168,191,204,212,226,272,295,309,322,326,327,380,388,390,413,433,435,450,451,472,477,482,490,520,524,547,562,576,579,598,599,625,634,639,686,688,690,701,713,729,744,752,772,773,787,799,813}'),
(12,'Začini i dodaci jelima','{10,46,80,87,132,145,175,198,281,299,308,351,357,376,408,417,443,471,577,582,622,628,643,662,666,674,676,717,742,758,781,808,824}'),
(13,'Juhe i gotova jela','{2,12,18,25,40,43,49,57,108,125,149,214,216,231,245,254,262,297,323,340,350,381,444,448,481,498,507,526,529,536,552,573,603,604,621,631,660,678,684,763,783,789}'),
(14,'Smrznuti program','{36,120,144,188,199,217,220,229,241,437,454,475,484,493,507,514,516,517,523,557,566,588,606,611,655,665,681,692,699,726,737,782}'),
(15,'Dječji program','{39,95,118,256,267,286,505,540,564,667,677,748,823}'),
(16,'Osobna higijena i kozmetika','{8,29,30,56,59,90,92,107,114,116,119,135,172,173,174,186,197,200,320,332,352,360,361,382,387,411,510,515,572,590,591,640,695,696,700,759,801,802}'),
(17,'Čišćenje i kućanstvo','{33,143,180,189,335,338,343,344,367,402,418,426,431,436,470,543,544,734,735,770,806,807,820}'),
(18,'Kućni ljubimci','{48,136,155,156,237,341,432,565,568,580,624,652,663,694,697,728,796}'),
(19,'Zdravlje i dodaci prehrani','{50,65,69,70,224,247,261,347,407,439,440,445,479,483,632,712,792,815}'),
(20,'Sve za dom i vrt','{11,21,97,109,131,137,150,158,205,258,259,279,317,356,383,384,442,467,469,485,532,558,561,597,630,648,691,707,723,733,738,803,817}'),
(21,'Veganska i bezglutenska hrana','{45,160,234,278,289,316,369,371,551,575,740}'),
(22,'Posebne namirnice i delikatese','{99,164,183,219,235,249,304,342,374,378,422,465,495,499,501,610,612,714}'),
(23,'Ostalo','{28,67,104,105,177,325,401,494,635,682,685,751,766}');

-- Add new category_id column to g_products
ALTER TABLE g_products ADD COLUMN category_id INTEGER;

-- Update g_products.category_id based on the remapping
UPDATE g_products gp
SET category_id = gc.id
FROM old_categories oc, g_categories gc
WHERE gp.old_category_id = oc.id
  AND oc.id = ANY(gc.array_of_old_ids);

-- Assign 'Ostalo' (id 23) to any g_products that still have a NULL category_id
UPDATE g_products
SET category_id = 23
WHERE category_id IS NULL;

-- Set category_id to NOT NULL and add foreign key
ALTER TABLE g_products ALTER COLUMN category_id SET NOT NULL;
ALTER TABLE g_products ADD CONSTRAINT fk_g_category FOREIGN KEY (category_id) REFERENCES g_categories(id);

-- Drop old columns/tables
ALTER TABLE g_products DROP COLUMN old_category_id;
DROP TABLE old_categories;

COMMIT;
