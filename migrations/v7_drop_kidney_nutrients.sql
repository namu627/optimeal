-- v7 (2026-10-11): ingredient_nutrient 에서 칼륨·인 칸 제거
--
-- 왜: 신장질환 대체식을 범위에서 뺐다(사용자 결정). 대한신장학회 자료에 칼륨·인의 하루 섭취량 수치가 없고
--     (혈중 목표만 있음) 단백질은 체중·병기별이라 급식 그룹에 걸 근거가 부족하다. 두 칸은 신장질환에만 쓰였다.
--     v6 은 이미 배포·적용됐을 수 있어 고치지 않고 이 파일로 지운다.
-- 적용 뒤: scripts/load_ingredient_nutrient.py --apply 로 다시 적재(CSV 에서도 두 칸을 뺐다).
-- 멱등: 몇 번 돌려도 같은 상태.

ALTER TABLE ingredient_nutrient DROP COLUMN IF EXISTS phosphorus_mg;
ALTER TABLE ingredient_nutrient DROP COLUMN IF EXISTS potassium_mg;
