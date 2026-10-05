import { describe, expect, it } from 'vitest';
import type { MenuRecipe } from '../../api/menu';
import { entryKey, parseSubstitution, substitutedRecipe } from './recipeView';

const rec: MenuRecipe = {
  cooking_method: '찜',
  ingredients: [{ name: '달걀', amount: 500 }, { name: '우유', amount: 200 }, { name: '대파', amount: 30 }],
  steps: ['1. 계란과 우유를 섞는다.', '2. 달걀물에 대파를 넣고 찐다.'],
};

describe('재료 치환 대체식 레시피', () => {
  it('이름에서 치환 쌍을 읽는다', () => {
    expect(parseSubstitution('달걀찜(대체: 달걀→두부, 우유→두유)')).toEqual([['달걀', '두부'], ['우유', '두유']]);
    expect(parseSubstitution('달걀찜')).toEqual([]);
  });

  it('재료 목록과 조리 순서에서 알레르기 재료를 바꾼다', () => {
    const out = substitutedRecipe(rec, '달걀찜(대체: 달걀→두부, 우유→두유)')!;
    expect(out.ingredients.map((i) => i.name)).toEqual(['두부(달걀 대신)', '두유(우유 대신)', '대파']);
    expect(out.ingredients[0].amount).toBe(500);
    expect(out.steps![0]).toContain('달걀→두부, 우유→두유');
    expect(out.steps!.slice(1)).toEqual([
      '1. 두부(계란 대신)과 두유(우유 대신)를 섞는다.',
      '2. 두부(달걀 대신)물에 대파를 넣고 찐다.',
    ]);
  });

  it('치환 접시가 아니면 그대로', () => {
    expect(substitutedRecipe(rec, '달걀찜')).toBe(rec);
  });

  it('치환 접시는 원래 메뉴와 다른 엔트리 키', () => {
    expect(entryKey({ name: '달걀찜', nutritionId: 1 }, 10)).toBe('id:1@10');
    expect(entryKey({ name: '달걀찜(대체: 달걀→두부)', nutritionId: 1 }, 10)).not.toBe('id:1@10');
  });
});
