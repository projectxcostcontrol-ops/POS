/**
 * Ids for things we create.
 *
 * These become Firestore document ids, which cannot contain "/" and must
 * be unique. Two places used to build them by slugifying the name the
 * user typed: a material called "น้ำปลา/ซีอิ๊ว" produced an id with a
 * slash in it and failed to save at all, and the version without a
 * timestamp quietly overwrote an existing material whenever two were
 * given the same name.
 *
 * A name is a label, not an identity. Nothing here needs the id to be
 * readable, so it doesn't try to be.
 */
export function newId(prefix = 'mat') {
  const rand = Math.random().toString(36).slice(2, 8);
  return `${prefix}_${Date.now().toString(36)}${rand}`;
}
