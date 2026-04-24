import sqlite3
import csv
import os

DB_PATH = os.path.expanduser("~/Desktop/中药靶点发现计划/tcm.db")
DATA_DIR = os.path.expanduser("~/Downloads/用所选项目新建的文件夹zy/")

def get_conn():
    return sqlite3.connect(DB_PATH)

def create_tables(conn):
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS compound (
        np_id TEXT PRIMARY KEY,
        name TEXT,
        pubchem_id TEXT,
        num_of_organism INTEGER,
        num_of_target INTEGER
    );

    CREATE TABLE IF NOT EXISTS species (
        org_id TEXT PRIMARY KEY,
        org_name TEXT,
        species_name TEXT,
        genus_name TEXT,
        family_name TEXT,
        kingdom_name TEXT
    );

    CREATE TABLE IF NOT EXISTS target (
        target_id TEXT PRIMARY KEY,
        target_name TEXT,
        target_type TEXT,
        organism TEXT,
        uniprot_id TEXT
    );

    CREATE TABLE IF NOT EXISTS compound_species (
        np_id TEXT,
        org_id TEXT,
        PRIMARY KEY (np_id, org_id)
    );

    CREATE TABLE IF NOT EXISTS compound_target (
        np_id TEXT,
        target_id TEXT,
        activity_type TEXT,
        activity_value REAL,
        activity_units TEXT,
        PRIMARY KEY (np_id, target_id, activity_type)
    );
    """)
    conn.commit()

def import_compounds(conn):
    path = DATA_DIR + "NPASS3.0_naturalproducts_generalinfo.txt"
    print("导入天然产物...")
    with open(path, encoding='utf-8') as f:
        reader = csv.DictReader(f, delimiter='\t')
        batch = []
        for row in reader:
            batch.append((
                row['np_id'], row['pref_name'], row.get('pubchem_id',''),
                int(row['num_of_organism']) if row['num_of_organism'].isdigit() else 0,
                int(row['num_of_target']) if row['num_of_target'].isdigit() else 0
            ))
            if len(batch) >= 1000:
                conn.executemany("INSERT OR IGNORE INTO compound VALUES (?,?,?,?,?)", batch)
                batch = []
        if batch:
            conn.executemany("INSERT OR IGNORE INTO compound VALUES (?,?,?,?,?)", batch)
    conn.commit()
    print(f"完成: {conn.execute('SELECT COUNT(*) FROM compound').fetchone()[0]} 条")

def import_species(conn):
    path = DATA_DIR + "NPASS3.0_species_info.txt"
    print("导入物种...")
    with open(path, encoding='utf-8') as f:
        reader = csv.DictReader(f, delimiter='\t')
        batch = []
        for row in reader:
            batch.append((
                row['org_id'], row['org_name'], row.get('species_name',''),
                row.get('genus_name',''), row.get('family_name',''), row.get('kingdom_name','')
            ))
            if len(batch) >= 1000:
                conn.executemany("INSERT OR IGNORE INTO species VALUES (?,?,?,?,?,?)", batch)
                batch = []
        if batch:
            conn.executemany("INSERT OR IGNORE INTO species VALUES (?,?,?,?,?,?)", batch)
    conn.commit()
    print(f"完成: {conn.execute('SELECT COUNT(*) FROM species').fetchone()[0]} 条")

def import_targets(conn):
    path = DATA_DIR + "NPASS3.0_target.txt"
    print("导入靶点...")
    with open(path, encoding='utf-8') as f:
        reader = csv.DictReader(f, delimiter='\t')
        batch = []
        for row in reader:
            batch.append((
                row['target_id'], row['target_name'], row.get('target_type',''),
                row.get('target_organism',''), row.get('uniprot_id','')
            ))
            if len(batch) >= 1000:
                conn.executemany("INSERT OR IGNORE INTO target VALUES (?,?,?,?,?)", batch)
                batch = []
        if batch:
            conn.executemany("INSERT OR IGNORE INTO target VALUES (?,?,?,?,?)", batch)
    conn.commit()
    print(f"完成: {conn.execute('SELECT COUNT(*) FROM target').fetchone()[0]} 条")

def import_compound_species(conn):
    path = DATA_DIR + "NPASS3.0_naturalproducts_species_pair.txt"
    print("导入天然产物-物种关系...")
    with open(path, encoding='utf-8') as f:
        reader = csv.DictReader(f, delimiter='\t')
        batch = []
        for row in reader:
            batch.append((row['np_id'], row['org_id']))
            if len(batch) >= 5000:
                conn.executemany("INSERT OR IGNORE INTO compound_species VALUES (?,?)", batch)
                batch = []
        if batch:
            conn.executemany("INSERT OR IGNORE INTO compound_species VALUES (?,?)", batch)
    conn.commit()
    print(f"完成: {conn.execute('SELECT COUNT(*) FROM compound_species').fetchone()[0]} 条")

def import_activities(conn):
    path = DATA_DIR + "NPASS3.0_activities.txt"
    print("导入活性数据（最大文件，需要几分钟）...")
    with open(path, encoding='utf-8') as f:
        reader = csv.DictReader(f, delimiter='\t')
        batch = []
        for row in reader:
            try:
                val = float(row['activity_value']) if row['activity_value'] not in ('n.a.','') else None
            except:
                val = None
            batch.append((
                row['np_id'], row['target_id'],
                row.get('activity_type',''), val, row.get('activity_units','')
            ))
            if len(batch) >= 5000:
                conn.executemany("INSERT OR IGNORE INTO compound_target VALUES (?,?,?,?,?)", batch)
                batch = []
        if batch:
            conn.executemany("INSERT OR IGNORE INTO compound_target VALUES (?,?,?,?,?)", batch)
    conn.commit()
    print(f"完成: {conn.execute('SELECT COUNT(*) FROM compound_target').fetchone()[0]} 条")

if __name__ == "__main__":
    conn = get_conn()
    create_tables(conn)
    import_compounds(conn)
    import_species(conn)
    import_targets(conn)
    import_compound_species(conn)
    import_activities(conn)
    conn.close()
    print("数据库构建完成！")
